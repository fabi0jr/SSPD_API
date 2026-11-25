"""Adaptador HTTP com fakes: contrato da API sem banco (CLAUDE.md, seção 8)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.dominio.entidades import Paciente
from tests.fakes import RepositorioPacientesFake
from tests.unit.apoio_http import AGORA, CHAVE, ORIGEM_DASHBOARD, Saude, criar_app_fake, montar_componentes


@pytest.fixture
def banco(banco):
    RepositorioPacientesFake(banco).criar(Paciente(id="P001", nome="Paciente 1"))
    return banco


@pytest.fixture
def app(banco, saude):
    return criar_app_fake(montar_componentes(banco, saude))


@pytest.fixture
def cliente(app) -> TestClient:
    return TestClient(app)


def corpo(**kwargs) -> dict:
    dados = {
        "id": str(uuid4()),
        "paciente_id": "P001",
        "fc": 82,
        "spo2": 97.4,
        "temp": 36.7,
        "ts": "2026-11-25T14:03:12.481Z",
    }
    dados.update(kwargs)
    return dados


def postar(cliente, dados, chave=CHAVE):
    headers = {"X-API-Key": chave} if chave is not None else {}
    return cliente.post("/leituras", json=dados, headers=headers)


# --- /health ---------------------------------------------------------------------

def test_health_com_banco_no_ar(cliente):
    r = cliente.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok"}


def test_health_com_banco_fora(cliente, saude):
    saude.no_ar = False
    r = cliente.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degradado", "db": "indisponivel"}


# --- POST /leituras: sucesso e idempotência --------------------------------------

def test_leitura_nova_201(cliente, banco):
    dados = corpo()
    r = postar(cliente, dados)
    assert r.status_code == 201
    assert r.json() == {"id": dados["id"], "nova": True}
    assert len(banco.leituras) == 1


def test_reenvio_200_sem_duplicar(cliente, banco):
    dados = corpo()
    postar(cliente, dados)
    r = postar(cliente, dados)
    assert r.status_code == 200
    assert r.json() == {"id": dados["id"], "nova": False}
    assert len(banco.leituras) == 1


def test_ts_com_offset_e_aceito(cliente):
    assert postar(cliente, corpo(ts="2026-11-25T10:03:12-04:00")).status_code == 201


# --- POST /leituras: autenticação ------------------------------------------------

@pytest.mark.parametrize("chave", [None, "", "errada", CHAVE + "x"])
def test_api_key_invalida_401(cliente, banco, chave):
    r = postar(cliente, corpo(), chave=chave)
    assert r.status_code == 401
    assert r.json() == {"detail": "API key inválida ou ausente"}
    assert banco.leituras == {}


def test_bearer_no_lugar_da_api_key_401(cliente):
    r = cliente.post("/leituras", json=corpo(), headers={"Authorization": f"Bearer {CHAVE}"})
    assert r.status_code == 401


def test_sem_api_key_e_corpo_invalido_continua_401(cliente):
    assert postar(cliente, corpo(fc="abc"), chave=None).status_code == 401


# --- POST /leituras: validação ---------------------------------------------------

@pytest.mark.parametrize(
    ("campo", "valor"),
    [("fc", 19), ("fc", 251), ("spo2", 49.9), ("spo2", 100.1), ("temp", 29.9), ("temp", 43.1)],
)
def test_fora_da_faixa_422_no_formato_do_fastapi(cliente, banco, campo, valor):
    r = postar(cliente, corpo(**{campo: valor}))
    assert r.status_code == 422
    [erro] = r.json()["detail"]
    assert erro["loc"] == ["body", campo]
    assert erro["type"] == "value_error"
    assert banco.leituras == {}


def test_ts_mais_de_5_min_no_futuro_422(cliente):
    futuro = (AGORA + timedelta(minutes=6)).isoformat()
    r = postar(cliente, corpo(ts=futuro))
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "ts"]


@pytest.mark.parametrize(
    "alteracao",
    [
        {"ts": "2026-11-25T14:03:12"},   # sem fuso
        {"ts": "ontem"},
        {"id": "nao-e-uuid"},
        {"fc": "abc"},
        {"fc": 82.5},
        {"spo2": None},
        {"paciente_id": ""},
        {"paciente_id": "P" * 11},
    ],
)
def test_formato_invalido_422(cliente, alteracao):
    assert postar(cliente, corpo(**alteracao)).status_code == 422


def test_campo_faltando_422(cliente):
    dados = corpo()
    del dados["temp"]
    r = postar(cliente, dados)
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "temp"]


def test_json_quebrado_422(cliente):
    r = cliente.post("/leituras", content=b"{nao e json", headers={"X-API-Key": CHAVE, "Content-Type": "application/json"})
    assert r.status_code == 422


def test_paciente_inexistente_404(cliente, banco):
    r = postar(cliente, corpo(paciente_id="P999"))
    assert r.status_code == 404
    assert "P999" in r.json()["detail"]
    assert banco.leituras == {}


# --- Erros internos e CORS -------------------------------------------------------

def test_erro_interno_nao_vaza_stack_trace(banco, saude):
    class Quebrado:
        def executar(self, leitura):
            raise RuntimeError("senha=segredo no meio do erro")

    app = criar_app_fake(montar_componentes(banco, saude, registrar_leitura=Quebrado()))
    r = postar(TestClient(app, raise_server_exceptions=False), corpo())
    assert r.status_code == 500
    assert r.json() == {"detail": "erro interno"}
    assert "segredo" not in r.text


def test_banco_indisponivel_vira_503_com_retry_after(banco, saude):
    class BancoFora(Exception):
        pass

    class Indisponivel:
        def executar(self, leitura):
            raise BancoFora("connection refused")

    app = criar_app_fake(
        montar_componentes(banco, saude, registrar_leitura=Indisponivel()),
        erros_indisponibilidade=(BancoFora,),
    )
    r = postar(TestClient(app), corpo())  # sem raise_server_exceptions: não pode virar 500
    assert r.status_code == 503
    assert r.headers["retry-after"] == "5"
    assert r.json() == {"detail": "banco de dados indisponível, tente novamente"}


def test_cors_permite_so_a_origem_configurada(cliente):
    def preflight(origem):
        return cliente.options(
            "/health",
            headers={"Origin": origem, "Access-Control-Request-Method": "GET"},
        )

    assert preflight(ORIGEM_DASHBOARD).headers.get("access-control-allow-origin") == ORIGEM_DASHBOARD
    assert "access-control-allow-origin" not in preflight("https://site-malicioso.exemplo").headers


# --- Swagger ---------------------------------------------------------------------

def test_swagger_e_contrato_publicados(cliente):
    assert cliente.get("/docs").status_code == 200
    contrato = cliente.get("/openapi.json").json()

    assert contrato["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey", "in": "header", "name": "X-API-Key",
        "description": "Chave dos sensores (SENSOR_API_KEY). Usada só em POST /leituras.",
    }
    post = contrato["paths"]["/leituras"]["post"]
    assert post["security"] == [{"APIKeyHeader": []}]
    assert {"200", "201", "401", "404", "422"} <= set(post["responses"])
    assert "security" not in contrato["paths"]["/health"]["get"]
    exemplo = contrato["components"]["schemas"]["LeituraEntrada"]["examples"][0]
    assert exemplo["paciente_id"] == "P001"
