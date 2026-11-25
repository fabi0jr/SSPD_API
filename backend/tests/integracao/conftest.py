"""Fixtures dos testes de integração (Postgres real).

Os testes usam TEST_DATABASE_URL, e nunca DATABASE_URL, porque as tabelas são
apagadas entre um teste e outro. Sem a variável, os testes são pulados.

Postgres descartável para rodar localmente (dados em memória):

    docker run -d --name sspd-pg-teste --tmpfs /var/lib/postgresql/data \\
      -e POSTGRES_USER=monitor -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=monitor_teste \\
      -p 127.0.0.1:55432:5432 postgres:16-alpine

    export TEST_DATABASE_URL=postgresql+psycopg://monitor:teste@localhost:55432/monitor_teste
    pytest tests/integracao
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.adaptadores.saida.postgres.db import criar_engine, criar_fabrica_sessoes
from app.adaptadores.saida.postgres.repositorios import RepositorioPacientesPg
from app.dominio.entidades import Paciente

BACKEND = Path(__file__).resolve().parents[2]
URL = os.environ.get("TEST_DATABASE_URL", "").strip()


def pytest_collection_modifyitems(config, items):
    pasta = Path(__file__).parent
    for item in items:
        if pasta in Path(item.fspath).parents:
            item.add_marker(pytest.mark.integracao)
            if not URL:
                item.add_marker(pytest.mark.skip(reason="TEST_DATABASE_URL não definida"))


def alembic(engine, *comando: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    with engine.begin() as conexao:
        cfg.attributes["connection"] = conexao
        getattr(command, comando[0])(cfg, *comando[1:])


@pytest.fixture(scope="session")
def engine():
    engine = criar_engine(URL)
    # Esquema criado pela migration (e não por metadata.create_all), para
    # testar a migration de verdade.
    alembic(engine, "downgrade", "base")
    alembic(engine, "upgrade", "head")
    yield engine
    engine.dispose()


@pytest.fixture
def sessoes(engine):
    with engine.begin() as conexao:
        conexao.execute(text("TRUNCATE alertas, leituras, pacientes, usuarios RESTART IDENTITY CASCADE"))
    return criar_fabrica_sessoes(engine)


@pytest.fixture
def pacientes(sessoes):
    """P001 e P002 já cadastrados."""
    repo = RepositorioPacientesPg(sessoes)
    for pid in ("P001", "P002"):
        repo.criar(Paciente(id=pid, nome=f"Paciente {pid}"))
    return repo
