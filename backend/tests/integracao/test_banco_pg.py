"""db.py (health check e retry de conexão) e a migration."""

import time

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.adaptadores.saida.postgres.db import aguardar_banco, criar_engine, verificar_banco
from app.adaptadores.saida.postgres.models import Base

# Porta 1 em localhost: conexão recusada na hora, sem depender de rede.
URL_FORA_DO_AR = "postgresql+psycopg://x:x@127.0.0.1:1/x"


def test_verificar_banco_no_ar(engine):
    assert verificar_banco(engine) is True


def test_verificar_banco_fora_do_ar():
    engine = criar_engine(URL_FORA_DO_AR)
    try:
        assert verificar_banco(engine) is False
    finally:
        engine.dispose()


def test_aguardar_banco_no_ar_retorna_logo(engine):
    aguardar_banco(engine, timeout=1)


def test_aguardar_banco_desiste_depois_do_timeout(caplog):
    engine = criar_engine(URL_FORA_DO_AR)
    inicio = time.monotonic()
    try:
        with pytest.raises(Exception):
            aguardar_banco(engine, timeout=1.0, espera_inicial=0.1)
    finally:
        engine.dispose()
    assert time.monotonic() - inicio < 3
    assert sum("nova tentativa" in r.message for r in caplog.records) >= 2


def test_migrations_batem_com_os_models(engine):
    """Falha se alguém mudar models.py e esquecer de criar a migration."""
    with engine.connect() as conexao:
        diferencas = compare_metadata(MigrationContext.configure(conexao), Base.metadata)
    assert diferencas == []


def test_indices_criados(engine):
    insp = inspect(engine)
    assert {i["name"] for i in insp.get_indexes("leituras")} >= {
        "ix_leituras_paciente_ts", "ix_leituras_pendentes",
    }
    assert {i["name"] for i in insp.get_indexes("alertas")} >= {
        "ix_alertas_status_criado_em", "uq_alertas_aberto_por_tipo",
    }
