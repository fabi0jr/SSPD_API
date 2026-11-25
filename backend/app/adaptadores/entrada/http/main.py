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
from fastapi.responses import JSONResponse

from app.adaptadores.entrada.http.deps import Componentes
from app.adaptadores.entrada.http.routers import health, leituras
from app.dominio.erros import (
    AlertaNaoEncontrado,
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


def _resposta_erro_interno(request: Request, erro: Exception) -> JSONResponse:
    # Bug de verdade. O uvicorn já registra o traceback completo; a resposta
    # nunca leva stack trace nem a mensagem da exceção.
    log.error("erro não tratado em %s %s (%s)", request.method, request.url.path, type(erro).__name__)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "erro interno"},
    )


def criar_app(
    componentes: Componentes,
    sensor_api_key: str,
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_exception_handler(ErroDominio, _resposta_erro_dominio)
    for tipo in erros_indisponibilidade:
        app.add_exception_handler(tipo, _resposta_indisponivel)
    app.add_exception_handler(Exception, _resposta_erro_interno)

    app.include_router(health.router)
    app.include_router(leituras.router)
    return app
