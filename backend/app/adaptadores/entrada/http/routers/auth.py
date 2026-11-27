from fastapi import APIRouter, Depends, Request, status

from app.adaptadores.entrada.http.deps import autenticar, cadastrar_usuario
from app.adaptadores.entrada.http.schemas import Cadastro, Erro, Login, Token, UsuarioSaida
from app.adaptadores.entrada.http.seguranca import criar_token
from app.aplicacao.autenticar import Autenticar
from app.aplicacao.cadastrar_usuario import CadastrarUsuario

router = APIRouter(prefix="/auth", tags=["autenticação"])


@router.post(
    "/login",
    response_model=Token,
    summary="Login do dashboard: devolve um token JWT",
    description=(
        "Envie o token nas demais rotas com o header `Authorization: Bearer <access_token>`. "
        "O token expira em `JWT_EXP_MINUTES` (padrão 60 min); depois disso, faça login de novo."
    ),
    responses={
        401: {"model": Erro, "description": "E-mail ou senha inválidos (a resposta não diz qual)"},
        422: {"model": Erro, "description": "Corpo em formato inválido"},
    },
)
def login(corpo: Login, request: Request, caso_de_uso: Autenticar = Depends(autenticar)) -> Token:
    usuario = caso_de_uso.executar(corpo.email, corpo.senha)
    return Token(access_token=criar_token(usuario.email, request.app.state.jwt))


@router.post(
    "/cadastro",
    status_code=status.HTTP_201_CREATED,
    response_model=UsuarioSaida,
    summary="Cadastra um usuário do dashboard",
    description=(
        "Rota pública. Não devolve token: em seguida, chame `POST /auth/login` com o mesmo "
        "e-mail e senha. O e-mail é guardado exatamente como enviado (o login diferencia maiúsculas)."
    ),
    responses={
        409: {"model": Erro, "description": "E-mail já cadastrado"},
        422: {"model": Erro, "description": "E-mail em formato inválido ou senha fora do tamanho (8 caracteres a 72 bytes)"},
    },
)
def cadastro(
    corpo: Cadastro, caso_de_uso: CadastrarUsuario = Depends(cadastrar_usuario)
) -> UsuarioSaida:
    usuario = caso_de_uso.executar(corpo.email, corpo.senha)
    return UsuarioSaida(id=usuario.id, email=usuario.email)
