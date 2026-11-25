"""Aplicação FastAPI (adaptador de entrada HTTP).

`criar_app` recebe tudo pronto: quem monta as implementações concretas é o
composicao.py. Entrypoint de produção (fábrica do uvicorn):

    uvicorn app.composicao:criar_app_http --factory --host 0.0.0.0 --port 8000

Swagger em /docs, ReDoc em /redoc, contrato em /openapi.json.
"""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.adaptadores.entrada.http.deps import Componentes
from app.adaptadores.entrada.http.routers import alertas, auth, health, leituras, pacientes
from app.adaptadores.entrada.http.seguranca import ConfigJwt
from app.dominio.erros import (
    AlertaNaoEncontrado,
    CredenciaisInvalidas,
    ErroDominio,
    LeituraInvalida,
    PacienteInvalido,
    PacienteJaExiste,
    PacienteNaoEncontrado,
)

log = logging.getLogger(__name__)

DESCRICAO = """
API do sistema distribuído de monitoramento de pacientes (dados **fictícios**).

- **Sensores** enviam leituras com o header `X-API-Key`.
- **Dashboard** usa `Authorization: Bearer <JWT>`, obtido em `POST /auth/login`.

Erros seguem o formato padrão do FastAPI: `{"detail": ...}`. Timestamps em ISO 8601 UTC.
"""

STATUS_POR_ERRO: dict[type[ErroDominio], int] = {
    LeituraInvalida: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PacienteInvalido: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PacienteNaoEncontrado: status.HTTP_404_NOT_FOUND,
    AlertaNaoEncontrado: status.HTTP_404_NOT_FOUND,
    PacienteJaExiste: status.HTTP_409_CONFLICT,
    CredenciaisInvalidas: status.HTTP_401_UNAUTHORIZED,
}


def _resposta_erro_dominio(_: Request, erro: ErroDominio) -> JSONResponse:
    codigo = next(
        (c for tipo, c in STATUS_POR_ERRO.items() if isinstance(erro, tipo)),
        status.HTTP_400_BAD_REQUEST,
    )
    if isinstance(erro, (LeituraInvalida, PacienteInvalido)):
        # Mesmo formato dos 422 do próprio FastAPI, para o cliente tratar um só.
        detalhe: str | list[dict] = [
            {"loc": ["body", erro.campo], "msg": erro.mensagem, "type": "value_error"}
        ]
    else:
        detalhe = str(erro)
    return JSONResponse(status_code=codigo, content={"detail": detalhe})


def _resposta_indisponivel(request: Request, erro: Exception) -> JSONResponse:
    # Falha esperada (banco fora): uma linha de log, sem traceback. Numa queda,
    # o simulador reenvia o buffer e um traceback por requisição afogaria o log.
    log.warning(
        "banco indisponível em %s %s (%s): 503",
        request.method, request.url.path, type(erro).__name__,
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "banco de dados indisponível, tente novamente"},
        headers={"Retry-After": "5"},
    )


async def _capturar_erro_interno(request: Request, call_next) -> Response:
    """Bug de verdade vira 500 {"detail": "erro interno"}.

    É um middleware, e não um exception handler de Exception, porque o Starlette
    responde esse handler fora do CORS: o 500 sairia sem Access-Control-Allow-Origin
    e o navegador do dashboard veria só um erro de rede. O traceback vai para o
    log; a resposta nunca leva stack trace nem a mensagem da exceção.
    """
    try:
        return await call_next(request)
    except Exception as erro:
        log.exception("erro não tratado em %s %s (%s)", request.method, request.url.path, type(erro).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "erro interno"},
        )


def criar_app(
    componentes: Componentes,
    sensor_api_key: str,
    jwt: ConfigJwt,
    cors_origins: list[str],
    ao_encerrar: Callable[[], None] | None = None,
    erros_indisponibilidade: tuple[type[Exception], ...] = (),
) -> FastAPI:
    """`erros_indisponibilidade`: exceções que significam "banco fora" (viram 503).

    Vêm do composicao.py, para este adaptador não precisar conhecer o SQLAlchemy.
    """
    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI):
        yield
        if ao_encerrar is not None:  # SIGTERM (docker stop): fecha o pool de conexões
            ao_encerrar()

    app = FastAPI(
        title="Monitoramento de Pacientes",
        version="1.0.0",
        description=DESCRICAO,
        lifespan=ciclo_de_vida,
    )
    app.state.componentes = componentes
    app.state.sensor_api_key = sensor_api_key
    app.state.jwt = jwt

    # Ordem importa: o último middleware adicionado é o mais externo. O CORS
    # fica por fora para que até o 500 leve os headers de CORS.
    app.middleware("http")(_capturar_erro_interno)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
        # Sem isto o navegador esconde o Retry-After do 503 de outra origem.
        expose_headers=["Retry-After"],
    )
    app.add_exception_handler(ErroDominio, _resposta_erro_dominio)
    for tipo in erros_indisponibilidade:
        app.add_exception_handler(tipo, _resposta_indisponivel)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(leituras.router)
    app.include_router(pacientes.router)
    app.include_router(alertas.router)
    return app
