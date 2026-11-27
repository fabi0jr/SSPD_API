"""API montada pelo composicao.py, como em produção, com Postgres real."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.adaptadores.saida.postgres.models import AlertaModel, LeituraModel
from app.composicao import criar_app_http
from app.dominio.entidades import Alerta, Severidade, TipoAlerta
from app.seed import semear
from tests.integracao.apoio import inserir_alerta
from tests.integracao.conftest import URL

CHAVE = "chave-integracao"
SEGREDO_JWT = "segredo-jwt-de-integracao-com-mais-de-32"
EMAIL = "admin@example.com"
SENHA = "senha-do-admin"


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setenv("SENSOR_API_KEY", CHAVE)
    monkeypatch.setenv("JWT_SECRET", SEGREDO_JWT)


@pytest.fixture
def cliente(monkeypatch, pacientes):
    monkeypatch.setenv("DATABASE_URL", URL)
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

    def banco_nunca_responde(engine):
        raise OSError("banco fora")

    monkeypatch.setattr("app.composicao.aguardar_banco", banco_nunca_responde)
    with TestClient(criar_app_http()) as cliente:
        assert cliente.get("/health").status_code == 503


def test_post_com_banco_fora_responde_503(monkeypatch):
    """Banco cai depois que a API subiu: POST /leituras responde 503, não 500."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")
    monkeypatch.setattr("app.composicao.aguardar_banco", lambda engine: None)
    corpo = {"id": str(uuid4()), "paciente_id": "P001", "fc": 82, "spo2": 97.4,
             "temp": 36.7, "ts": "2026-11-25T14:03:12.481Z"}
    with TestClient(criar_app_http()) as cliente:
        r = cliente.post("/leituras", json=corpo, headers={"X-API-Key": CHAVE})
    assert r.status_code == 503


@pytest.mark.parametrize("valor", [None, "curto"])
def test_sem_jwt_secret_valido_a_api_nao_sobe(monkeypatch, valor):
    monkeypatch.setenv("DATABASE_URL", URL)
    if valor is None:
        monkeypatch.delenv("JWT_SECRET")
    else:
        monkeypatch.setenv("JWT_SECRET", valor)
    with pytest.raises(Exception, match="JWT_SECRET"):
        criar_app_http()


# --- Dashboard: login com o usuário do seed e rotas protegidas -------------------

@pytest.fixture
def auth(cliente, engine) -> dict:
    """Admin criado pelo seed (bcrypt de verdade) e token obtido pelo login."""
    semear(engine, EMAIL, SENHA, n_pacientes=0)
    r = cliente.post("/auth/login", json={"email": EMAIL, "senha": SENHA})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def postar_leituras(cliente, paciente_id: str, n: int, **valores) -> list[str]:
    """Grava `n` leituras com ts crescente (de minuto em minuto) e devolve os ids."""
    t0 = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)
    ids = []
    for i in range(n):
        corpo = {"id": str(uuid4()), "paciente_id": paciente_id, "fc": 82, "spo2": 97.4,
                 "temp": 36.7, "ts": (t0 + timedelta(minutes=i)).isoformat()} | valores
        assert cliente.post("/leituras", json=corpo, headers={"X-API-Key": CHAVE}).status_code == 201
        ids.append(corpo["id"])
    return ids


def test_login_com_usuario_do_seed(cliente, engine):
    semear(engine, EMAIL, SENHA, n_pacientes=0)
    assert cliente.post("/auth/login", json={"email": EMAIL, "senha": SENHA}).status_code == 200
    assert cliente.post("/auth/login", json={"email": EMAIL, "senha": "errada"}).status_code == 401
    assert cliente.post("/auth/login", json={"email": "x@example.com", "senha": SENHA}).status_code == 401


def test_rota_protegida_sem_token_401(cliente):
    assert cliente.get("/pacientes").status_code == 401
    assert cliente.get("/alertas").status_code == 401


def test_listar_pacientes_com_resumo(cliente, auth, sessoes):
    ids = postar_leituras(cliente, "P001", 2)
    inserir_alerta(sessoes, Alerta("P001", ids[0], TipoAlerta.FEBRE, 38.5, Severidade.MEDIA))

    p1, p2 = cliente.get("/pacientes", headers=auth).json()
    assert (p1["id"], p1["ultima_leitura"]["id"], p1["alertas_abertos"]) == ("P001", ids[-1], 1)
    assert p1["ultima_leitura"]["spo2"] == 97.4
    assert p1["ultima_leitura"]["ts"] == "2026-11-25T14:01:00Z"
    assert (p2["id"], p2["ultima_leitura"], p2["alertas_abertos"]) == ("P002", None, 0)


def test_cadastrar_paciente_e_ultima_leitura(cliente, auth):
    r = cliente.post("/pacientes", json={"id": "P050", "nome": "Paciente 50"}, headers=auth)
    assert r.status_code == 201
    assert cliente.post("/pacientes", json={"id": "P050", "nome": "De novo"}, headers=auth).status_code == 409
    assert cliente.get("/pacientes/P050/ultima", headers=auth).json() is None

    [lid] = postar_leituras(cliente, "P050", 1)
    assert cliente.get("/pacientes/P050/ultima", headers=auth).json()["id"] == lid


def test_historico_respeita_limit_ordem_e_intervalo(cliente, auth):
    ids = postar_leituras(cliente, "P001", 5)
    postar_leituras(cliente, "P002", 3)  # não pode aparecer no histórico do P001

    r = cliente.get("/pacientes/P001/leituras", params={"limit": 3}, headers=auth)
    assert [l["id"] for l in r.json()] == ids[::-1][:3]

    todas = cliente.get("/pacientes/P001/leituras", headers=auth).json()
    assert [l["id"] for l in todas] == ids[::-1]

    params = {"desde": "2026-11-25T14:01:00Z", "ate": "2026-11-25T10:03:00-04:00"}
    r = cliente.get("/pacientes/P001/leituras", params=params, headers=auth)
    assert [l["id"] for l in r.json()] == [ids[3], ids[2], ids[1]]

    assert cliente.get("/pacientes/P999/leituras", headers=auth).status_code == 404


def test_listar_e_reconhecer_alertas(cliente, auth, sessoes):
    [lid] = postar_leituras(cliente, "P001", 1, spo2=87.5)
    alerta_id = inserir_alerta(sessoes, Alerta("P001", lid, TipoAlerta.SPO2_BAIXA, 87.5, Severidade.ALTA))

    [a] = cliente.get("/alertas", params={"status": "aberto"}, headers=auth).json()
    assert (a["id"], a["tipo"], a["valor"], a["status"]) == (alerta_id, "SPO2_BAIXA", 87.5, "aberto")

    for _ in range(2):  # idempotente
        r = cliente.patch(f"/alertas/{alerta_id}", json={"status": "reconhecido"}, headers=auth)
        assert (r.status_code, r.json()["status"]) == (200, "reconhecido")

    assert cliente.get("/alertas", params={"status": "aberto"}, headers=auth).json() == []
    with sessoes() as s:
        assert s.scalar(select(AlertaModel.status).where(AlertaModel.id == alerta_id)) == "reconhecido"
    assert cliente.patch("/alertas/999", json={"status": "reconhecido"}, headers=auth).status_code == 404


def test_reconhecer_libera_novo_alerta_do_mesmo_tipo(cliente, auth, sessoes):
    """O índice único parcial só vale para 'aberto': depois do reconhecimento cabe outro."""
    [l1, l2] = postar_leituras(cliente, "P001", 2, spo2=87.5)
    primeiro = inserir_alerta(sessoes, Alerta("P001", l1, TipoAlerta.SPO2_BAIXA, 87.5, Severidade.ALTA))
    cliente.patch(f"/alertas/{primeiro}", json={"status": "reconhecido"}, headers=auth)

    inserir_alerta(sessoes, Alerta("P001", l2, TipoAlerta.SPO2_BAIXA, 87.5, Severidade.ALTA))
    assert len(cliente.get("/alertas", params={"paciente_id": "P001"}, headers=auth).json()) == 2


# --- Cadastro de usuário ---------------------------------------------------------

def test_cadastro_depois_login_e_rota_protegida(cliente, engine):
    semear(engine, EMAIL, SENHA, n_pacientes=0)  # o admin fica com o id 1
    corpo = {"email": "fulano@exemplo.com", "senha": "senha-segura-123"}

    r = cliente.post("/auth/cadastro", json=corpo)
    assert (r.status_code, r.json()) == (201, {"id": 2, "email": "fulano@exemplo.com"})

    r = cliente.post("/auth/login", json=corpo)
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert cliente.get("/pacientes", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_cadastro_repetido_409_e_senha_nao_muda(cliente):
    corpo = {"email": "fulano@exemplo.com", "senha": "senha-segura-123"}
    assert cliente.post("/auth/cadastro", json=corpo).status_code == 201

    r = cliente.post("/auth/cadastro", json=corpo | {"senha": "outra-senha-456"})
    assert (r.status_code, r.json()) == (409, {"detail": "e-mail já cadastrado"})
    assert cliente.post("/auth/login", json=corpo).status_code == 200
    assert cliente.post("/auth/login", json=corpo | {"senha": "outra-senha-456"}).status_code == 401


def test_cadastro_com_banco_fora_responde_503(monkeypatch):
    """Banco fora: POST /auth/cadastro responde 503 (o bcrypt roda antes, o insert falha)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")
    monkeypatch.setattr("app.composicao.aguardar_banco", lambda engine: None)
    with TestClient(criar_app_http()) as cliente:
        r = cliente.post("/auth/cadastro", json={"email": "fulano@exemplo.com", "senha": "senha-segura-123"})
    assert r.status_code == 503
    assert r.headers["retry-after"] == "5"
