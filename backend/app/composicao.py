"""Composição (injeção de dependência manual).

Único lugar que conhece as implementações concretas: liga os adaptadores de
saída (Postgres) aos casos de uso e entrega tudo aos adaptadores de entrada.
"""

import logging
import sys
from datetime import datetime, timezone

from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app import config
from app.adaptadores.entrada.http.deps import Componentes
from app.adaptadores.entrada.http.main import criar_app
from app.adaptadores.saida.postgres.db import (
    aguardar_banco,
    criar_engine,
    criar_fabrica_sessoes,
    verificar_banco,
)
from app.adaptadores.saida.postgres.repositorios import RepositorioLeiturasPg, RepositorioPacientesPg
from app.aplicacao.registrar_leitura import RegistrarLeitura

log = logging.getLogger("composicao")


def relogio_utc() -> datetime:
    return datetime.now(timezone.utc)


def configurar_logs() -> None:
    """Uma linha por evento, em stdout, com timestamp (CLAUDE.md, seção 14)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
    )


def montar_componentes_http(engine: Engine) -> Componentes:
    sessoes = criar_fabrica_sessoes(engine)
    return Componentes(
        registrar_leitura=RegistrarLeitura(
            pacientes=RepositorioPacientesPg(sessoes),
            leituras=RepositorioLeiturasPg(sessoes),
            relogio=relogio_utc,
        ),
        verificar_banco=lambda: verificar_banco(engine),
    )


def criar_app_http() -> FastAPI:
    """Fábrica para o uvicorn (--factory). Lê o ambiente e monta a API."""
    configurar_logs()
    # Variáveis obrigatórias primeiro: sem elas, falhar já no start é o certo.
    sensor_api_key = config.obrigatoria("SENSOR_API_KEY")
    engine = criar_engine(config.database_url())

    try:
        aguardar_banco(engine)
    except Exception:
        # A API sobe mesmo assim: /health responde 503 e o pool reconecta
        # sozinho quando o banco voltar. Melhor do que reiniciar em loop.
        log.error("banco ainda indisponível; API subindo em modo degradado")

    return criar_app(
        montar_componentes_http(engine),
        sensor_api_key=sensor_api_key,
        cors_origins=config.lista("CORS_ORIGINS"),
        ao_encerrar=engine.dispose,
        # Conexão recusada/caída e pool esgotado: o banco está fora ou sobrecarregado.
        erros_indisponibilidade=(OperationalError, PoolTimeoutError),
    )
