"""API montada pelo composicao.py, como em produção, com Postgres real."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.adaptadores.saida.postgres.models import LeituraModel
from app.composicao import criar_app_http
from tests.integracao.conftest import URL

CHAVE = "chave-integracao"


@pytest.fixture
def cliente(monkeypatch, pacientes):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("SENSOR_API_KEY", CHAVE)
    monkeypatch.setenv("CORS_ORIGINS", "https://dashboard.exemplo")
    with TestClient(criar_app_http()) as cliente:  # `with` roda o lifespan (dispose no fim)
        yield cliente


def test_health_200(cliente):
    r = cliente.get("/health")
    assert (r.status_code, r.json()) == (200, {"status": "ok", "db": "ok"})


def test_post_leituras_201_200_e_grava_uma_vez(cliente, sessoes):
    corpo = {"id": str(uuid4()), "paciente_id": "P001", "fc": 82, "spo2": 97.4,
             "temp": 36.7, "ts": "2026-11-25T14:03:12.481Z"}
    headers = {"X-API-Key": CHAVE}

    assert cliente.post("/leituras", json=corpo, headers=headers).status_code == 201
    assert cliente.post("/leituras", json=corpo, headers=headers).status_code == 200

    with sessoes() as s:
        assert s.scalar(select(func.count()).select_from(LeituraModel)) == 1
        assert s.scalar(select(func.count(func.distinct(LeituraModel.id)))) == 1


def test_post_leituras_erros(cliente, sessoes):
    corpo = {"id": str(uuid4()), "paciente_id": "P999", "fc": 82, "spo2": 97.4,
             "temp": 36.7, "ts": "2026-11-25T14:03:12.481Z"}

    assert cliente.post("/leituras", json=corpo, headers={"X-API-Key": "errada"}).status_code == 401
    assert cliente.post("/leituras", json=corpo, headers={"X-API-Key": CHAVE}).status_code == 404
    corpo.update(paciente_id="P001", spo2=40)
    assert cliente.post("/leituras", json=corpo, headers={"X-API-Key": CHAVE}).status_code == 422

    with sessoes() as s:
        assert s.scalar(select(func.count()).select_from(LeituraModel)) == 0


def test_sem_sensor_api_key_a_api_nao_sobe(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("SENSOR_API_KEY", raising=False)
    with pytest.raises(Exception, match="SENSOR_API_KEY"):
        criar_app_http()


def test_api_sobe_em_modo_degradado_sem_banco(monkeypatch):
    """Se o banco não voltar no tempo do retry, a API sobe e responde 503."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")
    monkeypatch.setenv("SENSOR_API_KEY", CHAVE)

    def banco_nunca_responde(engine):
        raise OSError("banco fora")

    monkeypatch.setattr("app.composicao.aguardar_banco", banco_nunca_responde)
    with TestClient(criar_app_http()) as cliente:
        assert cliente.get("/health").status_code == 503


def test_post_com_banco_fora_responde_503(monkeypatch):
    """Banco cai depois que a API subiu: POST /leituras responde 503, não 500."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")
    monkeypatch.setenv("SENSOR_API_KEY", CHAVE)
    monkeypatch.setattr("app.composicao.aguardar_banco", lambda engine: None)
    corpo = {"id": str(uuid4()), "paciente_id": "P001", "fc": 82, "spo2": 97.4,
             "temp": 36.7, "ts": "2026-11-25T14:03:12.481Z"}
    with TestClient(criar_app_http()) as cliente:
        r = cliente.post("/leituras", json=corpo, headers={"X-API-Key": CHAVE})
    assert r.status_code == 503
