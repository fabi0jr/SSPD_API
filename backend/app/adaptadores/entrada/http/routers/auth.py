from fastapi import APIRouter, Depends, Request

from app.adaptadores.entrada.http.deps import autenticar
from app.adaptadores.entrada.http.schemas import Erro, Login, Token
from app.adaptadores.entrada.http.seguranca import criar_token
from app.aplicacao.autenticar import Autenticar

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
