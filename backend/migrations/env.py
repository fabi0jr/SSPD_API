"""Ambiente do Alembic.

A URL vem de DATABASE_URL e é passada direto ao create_engine, sem passar pelo
alembic.ini: o configparser quebra com '%' (senha com caractere especial
codificado na URL, ex. %40 para '@').

Nos testes, uma conexão pronta chega por config.attributes["connection"].
"""

import logging
from logging.config import fileConfig

from alembic import context

from app import config as config_app
from app.adaptadores.saida.postgres.db import aguardar_banco, criar_engine
from app.adaptadores.saida.postgres.models import Base

config = context.config
if config.config_file_name is not None and not config.attributes.get("connection"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _rodar(conexao) -> None:
    context.configure(connection=conexao, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def rodar_online() -> None:
    conexao = config.attributes.get("connection")
    if conexao is not None:
        _rodar(conexao)
        return

    engine = criar_engine(config_app.database_url(), pool_size=1, max_overflow=0)
    try:
        aguardar_banco(engine)  # o serviço migrate pode subir antes do Postgres
        with engine.connect() as conexao:
            _rodar(conexao)
    finally:
        engine.dispose()


if context.is_offline_mode():
    logging.getLogger(__name__).error("modo offline não é suportado; rode 'alembic upgrade head'")
    raise SystemExit(1)

rodar_online()
