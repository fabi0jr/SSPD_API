"""Autenticação do adaptador HTTP. Não tem porta: é preocupação só da borda.

- Sensores: header X-API-Key (só em POST /leituras).
- Dashboard: JWT (etapa 5).

Os esquemas usam as classes de segurança do FastAPI para o Swagger mostrar
o botão "Authorize".
"""

import hmac

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

esquema_api_key = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,  # o 401 é nosso, com mensagem padronizada
    description="Chave dos sensores (SENSOR_API_KEY). Usada só em POST /leituras.",
)


def exigir_api_key(request: Request, chave: str | None = Security(esquema_api_key)) -> None:
    esperada: str = request.app.state.sensor_api_key
    # compare_digest: tempo constante, não vaza quantos caracteres batem.
    if not chave or not hmac.compare_digest(chave.encode(), esperada.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou ausente",
            headers={"WWW-Authenticate": "ApiKey"},
        )
