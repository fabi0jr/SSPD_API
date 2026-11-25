"""Rotas do dashboard com fakes: login, JWT, pacientes e alertas (CLAUDE.md, seção 8)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.adaptadores.entrada.http.seguranca import ConfigJwt, criar_token
from app.dominio.entidades import Alerta, Leitura, Paciente, Severidade, StatusAlerta, TipoAlerta, Usuario
from tests.fakes import RepositorioLeiturasFake, RepositorioPacientesFake
from tests.unit.apoio_http import JWT, criar_app_fake, montar_componentes

EMAIL = "admin@example.com"
SENHA = "senha-certa"
T0 = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def banco(banco):
    pacientes = RepositorioPacientesFake(banco)
    pacientes.criar(Paciente(id="P001", nome="Paciente 1", leito="L-001"))
    pacientes.criar(Paciente(id="P002", nome="Paciente 2"))
    senha_hash = bcrypt.hashpw(SENHA.encode(), bcrypt.gensalt(rounds=4)).decode()
    banco.usuarios[EMAIL] = Usuario(1, EMAIL, senha_hash)
    return banco


@pytest.fixture
def cliente(banco, saude) -> TestClient:
    return TestClient(criar_app_fake(montar_componentes(banco, saude)))


@pytest.fixture
def auth() -> dict:
    return {"Authorization": f"Bearer {criar_token(EMAIL, JWT)}"}


def gravar_leituras(banco, paciente_id: str, n: int) -> list[Leitura]:
    repo = RepositorioLeiturasFake(banco)
    leituras = [Leitura(uuid4(), paciente_id, 80, 97.4, 36.7, T0 + timedelta(minutes=i)) for i in range(n)]
    for l in leituras:
        repo.salvar_se_nova(l)
    return leituras


def gravar_alerta(banco, leitura: Leitura, tipo=TipoAlerta.FEBRE, status=StatusAlerta.ABERTO) -> Alerta:
    return banco.inserir_alerta(
        Alerta(leitura.paciente_id, leitura.id, tipo, 38.5, Severidade.MEDIA, status=status, criado_em=T0)
    )


# --- POST /auth/login ------------------------------------------------------------

def test_login_correto_devolve_token_valido(cliente):
    r = cliente.post("/auth/login", json={"email": EMAIL, "senha": SENHA})
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["token_type"] == "bearer"
    claims = pyjwt.decode(corpo["access_token"], JWT.segredo, algorithms=["HS256"])
    assert claims["sub"] == EMAIL
    assert claims["exp"] - claims["iat"] == 60 * 60


@pytest.mark.parametrize(
    "corpo", [{"email": EMAIL, "senha": "errada"}, {"email": "ninguem@example.com", "senha": SENHA}]
)
def test_login_errado_401_sem_dizer_o_que_errou(cliente, corpo):
    r = cliente.post("/auth/login", json=corpo)
    assert r.status_code == 401
    assert r.json() == {"detail": "e-mail ou senha inválidos"}


@pytest.mark.parametrize("corpo", [{}, {"email": EMAIL}, {"email": EMAIL, "senha": ""}])
def test_login_corpo_invalido_422(cliente, corpo):
    assert cliente.post("/auth/login", json=corpo).status_code == 422


def test_token_do_login_abre_rota_protegida(cliente):
    token = cliente.post("/auth/login", json={"email": EMAIL, "senha": SENHA}).json()["access_token"]
    assert cliente.get("/pacientes", headers={"Authorization": f"Bearer {token}"}).status_code == 200


# --- JWT nas rotas protegidas ----------------------------------------------------

ROTAS_PROTEGIDAS = [
    ("get", "/pacientes"),
    ("post", "/pacientes"),
    ("get", "/pacientes/P001/ultima"),
    ("get", "/pacientes/P001/leituras"),
    ("get", "/alertas"),
    ("patch", "/alertas/1"),
]


def _token_expirado() -> str:
    return criar_token(EMAIL, JWT, agora=datetime.now(timezone.utc) - timedelta(hours=2))


def _token_outro_segredo() -> str:
    return criar_token(EMAIL, ConfigJwt(segredo="x" * 40))


def _token_alg_none() -> str:
    return pyjwt.encode({"sub": EMAIL, "exp": 9999999999}, key=None, algorithm="none")


@pytest.mark.parametrize(("metodo", "rota"), ROTAS_PROTEGIDAS)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer nao-e-um-jwt"},
        {"Authorization": "Basic YWRtaW46c2VuaGE="},
        {"X-API-Key": "chave-de-teste"},  # a chave dos sensores não abre o dashboard
    ],
    ids=["sem-header", "bearer-vazio", "lixo", "basic", "api-key"],
)
def test_rota_protegida_sem_token_valido_401(cliente, metodo, rota, headers):
    r = getattr(cliente, metodo)(rota, headers=headers)
    assert r.status_code == 401
    assert r.json() == {"detail": "token inválido ou ausente"}
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("token", [_token_expirado, _token_outro_segredo, _token_alg_none])
def test_token_expirado_falsificado_ou_sem_assinatura_401(cliente, token):
    assert cliente.get("/pacientes", headers={"Authorization": f"Bearer {token()}"}).status_code == 401


def test_sem_token_e_corpo_invalido_continua_401(cliente):
    assert cliente.post("/pacientes", json={"id": ""}).status_code == 401


def test_jwt_secret_curto_e_rejeitado():
    with pytest.raises(ValueError, match="32"):
        ConfigJwt(segredo="curto")


# --- /pacientes ------------------------------------------------------------------

def test_listar_pacientes_com_resumo(cliente, banco, auth):
    leituras = gravar_leituras(banco, "P001", 2)
    gravar_alerta(banco, leituras[0])

    r = cliente.get("/pacientes", headers=auth)
    assert r.status_code == 200
    p1, p2 = r.json()
    assert (p1["id"], p1["leito"], p1["alertas_abertos"]) == ("P001", "L-001", 1)
    assert p1["ultima_leitura"]["id"] == str(leituras[-1].id)
    assert (p2["id"], p2["ultima_leitura"], p2["alertas_abertos"]) == ("P002", None, 0)


def test_timestamps_saem_em_utc_com_z(cliente, banco, auth):
    repo = RepositorioLeiturasFake(banco)
    fuso_manaus = timezone(timedelta(hours=-4))
    repo.salvar_se_nova(Leitura(uuid4(), "P001", 80, 97.4, 36.7, datetime(2026, 11, 25, 10, 0, tzinfo=fuso_manaus)))

    corpo = cliente.get("/pacientes/P001/ultima", headers=auth).json()
    assert corpo["ts"] == "2026-11-25T14:00:00Z"
    assert corpo["recebido_em"].endswith("Z")


def test_cadastrar_paciente_201(cliente, banco, auth):
    r = cliente.post("/pacientes", json={"id": "P010", "nome": "Paciente 10", "leito": "L-010"}, headers=auth)
    assert r.status_code == 201
    corpo = r.json()
    assert (corpo["id"], corpo["nome"], corpo["leito"]) == ("P010", "Paciente 10", "L-010")
    assert corpo["criado_em"].endswith("Z")
    assert "P010" in banco.pacientes


def test_cadastrar_paciente_repetido_409(cliente, auth):
    r = cliente.post("/pacientes", json={"id": "P001", "nome": "Outro"}, headers=auth)
    assert r.status_code == 409
    assert "P001" in r.json()["detail"]


@pytest.mark.parametrize(
    "corpo",
    [
        {"id": "", "nome": "X"},
        {"id": "P" * 11, "nome": "X"},
        {"id": "P011", "nome": ""},
        {"id": "P011", "nome": "   "},  # passa no Pydantic, cai no domínio
        {"id": "P011", "nome": "X", "leito": "L" * 21},
        {"nome": "X"},
    ],
)
def test_cadastrar_paciente_invalido_422(cliente, auth, corpo):
    r = cliente.post("/pacientes", json=corpo, headers=auth)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)


def test_ultima_leitura(cliente, banco, auth):
    leituras = gravar_leituras(banco, "P001", 3)
    r = cliente.get("/pacientes/P001/ultima", headers=auth)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["id"] == str(leituras[-1].id)
    assert set(corpo) == {"id", "paciente_id", "fc", "spo2", "temp", "ts", "recebido_em", "processada"}


def test_ultima_leitura_sem_leituras_e_null(cliente, auth):
    r = cliente.get("/pacientes/P002/ultima", headers=auth)
    assert (r.status_code, r.json()) == (200, None)


@pytest.mark.parametrize("rota", ["/pacientes/P999/ultima", "/pacientes/P999/leituras"])
def test_paciente_inexistente_404(cliente, auth, rota):
    r = cliente.get(rota, headers=auth)
    assert r.status_code == 404
    assert "P999" in r.json()["detail"]


def test_historico_ordem_desc_e_limit(cliente, banco, auth):
    leituras = gravar_leituras(banco, "P001", 5)
    r = cliente.get("/pacientes/P001/leituras", params={"limit": 2}, headers=auth)
    assert r.status_code == 200
    assert [l["id"] for l in r.json()] == [str(leituras[4].id), str(leituras[3].id)]


def test_historico_desde_e_ate(cliente, banco, auth):
    leituras = gravar_leituras(banco, "P001", 5)
    params = {"desde": "2026-11-25T14:01:00Z", "ate": "2026-11-25T10:03:00-04:00"}
    r = cliente.get("/pacientes/P001/leituras", params=params, headers=auth)
    assert [l["id"] for l in r.json()] == [str(leituras[i].id) for i in (3, 2, 1)]


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 1001}, {"limit": "abc"}, {"desde": "ontem"}, {"desde": "2026-11-25T14:00:00"}]
)
def test_historico_parametros_invalidos_422(cliente, auth, params):
    assert cliente.get("/pacientes/P001/leituras", params=params, headers=auth).status_code == 422


# --- /alertas --------------------------------------------------------------------

def test_listar_alertas_com_filtros(cliente, banco, auth):
    l1 = gravar_leituras(banco, "P001", 1)[0]
    l2 = gravar_leituras(banco, "P002", 1)[0]
    a1 = gravar_alerta(banco, l1)
    gravar_alerta(banco, l1, TipoAlerta.TAQUICARDIA, StatusAlerta.RECONHECIDO)
    gravar_alerta(banco, l2)

    def ids(**params):
        r = cliente.get("/alertas", params=params, headers=auth)
        assert r.status_code == 200
        return [a["id"] for a in r.json()]

    assert len(ids()) == 3
    assert ids(status="aberto", paciente_id="P001") == [a1.id]
    assert len(ids(status="reconhecido")) == 1
    assert len(ids(limit=1)) == 1


def test_formato_do_alerta(cliente, banco, auth):
    alerta = gravar_alerta(banco, gravar_leituras(banco, "P001", 1)[0])
    [corpo] = cliente.get("/alertas", headers=auth).json()
    assert corpo == {
        "id": alerta.id,
        "paciente_id": "P001",
        "leitura_id": str(alerta.leitura_id),
        "tipo": "FEBRE",
        "valor": 38.5,
        "severidade": "MEDIA",
        "status": "aberto",
        "criado_em": "2026-11-25T14:00:00Z",
    }


@pytest.mark.parametrize("params", [{"status": "fechado"}, {"limit": 0}, {"limit": 1001}])
def test_listar_alertas_parametros_invalidos_422(cliente, auth, params):
    assert cliente.get("/alertas", params=params, headers=auth).status_code == 422


def test_reconhecer_alerta_e_idempotente(cliente, banco, auth):
    alerta = gravar_alerta(banco, gravar_leituras(banco, "P001", 1)[0])
    rota = f"/alertas/{alerta.id}"

    for _ in range(2):
        r = cliente.patch(rota, json={"status": "reconhecido"}, headers=auth)
        assert r.status_code == 200
        assert r.json()["status"] == "reconhecido"
    assert banco.alertas[alerta.id].status == StatusAlerta.RECONHECIDO


def test_reconhecer_alerta_inexistente_404(cliente, auth):
    r = cliente.patch("/alertas/999", json={"status": "reconhecido"}, headers=auth)
    assert r.status_code == 404
    assert "999" in r.json()["detail"]


@pytest.mark.parametrize("corpo", [{"status": "aberto"}, {"status": "qualquer"}, {}])
def test_reconhecer_com_status_invalido_422(cliente, banco, auth, corpo):
    alerta = gravar_alerta(banco, gravar_leituras(banco, "P001", 1)[0])
    assert cliente.patch(f"/alertas/{alerta.id}", json=corpo, headers=auth).status_code == 422
    assert banco.alertas[alerta.id].status == StatusAlerta.ABERTO


# --- Swagger ---------------------------------------------------------------------

def test_swagger_documenta_jwt_nas_rotas_do_dashboard(cliente):
    contrato = cliente.get("/openapi.json").json()
    assert contrato["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    for metodo, rota in ROTAS_PROTEGIDAS:
        rota_doc = rota.replace("P001", "{paciente_id}").replace("/1", "/{alerta_id}")
        assert contrato["paths"][rota_doc][metodo]["security"] == [{"HTTPBearer": []}]
    assert "security" not in contrato["paths"]["/auth/login"]["post"]
    for schema in ("Login", "Token", "PacienteEntrada", "ResumoPacienteSaida", "AlertaSaida", "LeituraSaida"):
        assert contrato["components"]["schemas"][schema].get("examples"), schema


# --- CORS para o dashboard (protocolo em docs/integracao-dashboard.md) -----------

@pytest.mark.parametrize("metodo", ["GET", "POST", "PATCH"])
def test_preflight_do_dashboard_com_authorization(cliente, metodo):
    r = cliente.options(
        "/alertas/1",
        headers={
            "Origin": "https://dashboard.exemplo",
            "Access-Control-Request-Method": metodo,
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://dashboard.exemplo"
    assert "Authorization" in r.headers["access-control-allow-headers"]


def test_401_leva_header_cors_para_o_front_ler_o_erro(cliente):
    r = cliente.get("/pacientes", headers={"Origin": "https://dashboard.exemplo"})
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == "https://dashboard.exemplo"
