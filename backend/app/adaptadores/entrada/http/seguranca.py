"""Autenticação do adaptador HTTP. Não tem porta: é preocupação só da borda.

- Sensores: header X-API-Key (só em POST /leituras).
- Dashboard: Authorization: Bearer <JWT> (HS256), obtido em POST /auth/login.
- Senhas: bcrypt (o hash é gerado pelo seed ou pelo POST /auth/cadastro).

Os esquemas usam as classes de segurança do FastAPI para o Swagger mostrar
o botão "Authorize".
"""

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

ALGORITMO_JWT = "HS256"
# HS256 com chave menor que 32 bytes é fraca (RFC 7518, seção 3.2).
TAMANHO_MINIMO_SEGREDO = 32

esquema_api_key = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,  # o 401 é nosso, com mensagem padronizada
    description="Chave dos sensores (SENSOR_API_KEY). Usada só em POST /leituras.",
)

esquema_bearer = HTTPBearer(
    auto_error=False,
    description="Token JWT obtido em POST /auth/login. Cole só o token, sem 'Bearer'.",
)


# --- API key dos sensores --------------------------------------------------------

def exigir_api_key(request: Request, chave: str | None = Security(esquema_api_key)) -> None:
    esperada: str = request.app.state.sensor_api_key
    # compare_digest: tempo constante, não vaza quantos caracteres batem.
    if not chave or not hmac.compare_digest(chave.encode(), esperada.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou ausente",
            headers={"WWW-Authenticate": "ApiKey"},
        )


# --- Senhas ----------------------------------------------------------------------

# Hash de uma senha qualquer, usado quando o e-mail não existe: o bcrypt roda do
# mesmo jeito e o tempo de resposta não revela quais e-mails estão cadastrados.
_HASH_FICTICIO = bcrypt.hashpw(b"senha-ficticia", bcrypt.gensalt()).decode("ascii")


def gerar_hash_senha(senha: str, rounds: int = 12) -> str:
    """Implementa `aplicacao.cadastrar_usuario.GerarHashSenha`. `rounds` menor só nos testes.

    A senha já chega validada (no máximo 72 bytes): o bcrypt 5 recusa senha maior.
    """
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("ascii")


def verificar_senha(senha: str, senha_hash: str | None) -> bool:
    """Implementa `aplicacao.autenticar.VerificarSenha`."""
    alvo = senha_hash or _HASH_FICTICIO
    try:
        confere = bcrypt.checkpw(senha.encode("utf-8"), alvo.encode("ascii"))
    except ValueError:  # hash corrompido no banco: nega, não quebra
        return False
    return confere and senha_hash is not None


# --- JWT -------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfigJwt:
    segredo: str
    expiracao_minutos: int = 60

    def __post_init__(self) -> None:
        if len(self.segredo.encode()) < TAMANHO_MINIMO_SEGREDO:
            raise ValueError(f"JWT_SECRET precisa ter pelo menos {TAMANHO_MINIMO_SEGREDO} caracteres")
        if self.expiracao_minutos <= 0:
            raise ValueError("JWT_EXP_MINUTES precisa ser positivo")


def criar_token(assunto: str, config: ConfigJwt, agora: datetime | None = None) -> str:
    """`assunto` vai no claim `sub` (o e-mail do usuário)."""
    agora = agora or datetime.now(timezone.utc)
    claims = {
        "sub": assunto,
        "iat": agora,
        "exp": agora + timedelta(minutes=config.expiracao_minutos),
    }
    return jwt.encode(claims, config.segredo, algorithm=ALGORITMO_JWT)


def _nao_autenticado() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="token inválido ou ausente",
        headers={"WWW-Authenticate": "Bearer"},
    )


def exigir_jwt(
    request: Request,
    credenciais: HTTPAuthorizationCredentials | None = Security(esquema_bearer),
) -> str:
    """Devolve o `sub` do token. 401 se ausente, malformado, expirado ou com assinatura errada."""
    if credenciais is None or credenciais.scheme.lower() != "bearer":
        raise _nao_autenticado()
    config: ConfigJwt = request.app.state.jwt
    try:
        # `algorithms` fixo: impede o ataque de trocar o alg para "none".
        claims = jwt.decode(
            credenciais.credentials,
            config.segredo,
            algorithms=[ALGORITMO_JWT],
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError:
        raise _nao_autenticado()
    return claims["sub"]
