"""POST /auth/cadastro com fakes (contrato em .claude/rules/api-http.md)."""

import bcrypt
import pytest
from fastapi.testclient import TestClient

from app.aplicacao.cadastrar_usuario import CadastrarUsuario
from tests.unit.apoio_http import criar_app_fake, montar_componentes

EMAIL = "fulano@exemplo.com"
SENHA = "senha-segura-123"


@pytest.fixture
def cliente(banco, saude) -> TestClient:
    return TestClient(criar_app_fake(montar_componentes(banco, saude)))


def test_cadastro_201_sem_senha_nem_hash(cliente, banco):
    r = cliente.post("/auth/cadastro", json={"email": EMAIL, "senha": SENHA})

    assert (r.status_code, r.json()) == (201, {"id": 1, "email": EMAIL})
    gravado = banco.usuarios[EMAIL]
    assert bcrypt.checkpw(SENHA.encode(), gravado.senha_hash.encode())  # bcrypt de verdade
    assert SENHA not in r.text and gravado.senha_hash not in r.text


def test_cadastro_e_depois_login_com_as_mesmas_credenciais(cliente):
    assert cliente.post("/auth/cadastro", json={"email": EMAIL, "senha": SENHA}).status_code == 201

    r = cliente.post("/auth/login", json={"email": EMAIL, "senha": SENHA})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert cliente.get("/pacientes", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_cadastro_nao_devolve_token(cliente):
    corpo = cliente.post("/auth/cadastro", json={"email": EMAIL, "senha": SENHA}).json()
    assert "access_token" not in corpo


def test_email_repetido_409(cliente, banco):
    cliente.post("/auth/cadastro", json={"email": EMAIL, "senha": SENHA})
    original = banco.usuarios[EMAIL]

    r = cliente.post("/auth/cadastro", json={"email": EMAIL, "senha": "outra-senha-456"})

    assert (r.status_code, r.json()) == (409, {"detail": "e-mail já cadastrado"})
    assert banco.usuarios[EMAIL] == original


@pytest.mark.parametrize(
    ("corpo", "campo"),
    [
        ({"email": EMAIL, "senha": "curta12"}, "senha"),                 # 7 caracteres
        ({"email": EMAIL, "senha": "á" * 37}, "senha"),                  # 37 caracteres, 74 bytes
        ({"email": EMAIL, "senha": "a" * 73}, "senha"),
        ({"email": "sem-arroba.com", "senha": SENHA}, "email"),
        ({"email": "fulano@exemplo", "senha": SENHA}, "email"),
        ({"email": "ful ano@exemplo.com", "senha": SENHA}, "email"),
        ({"email": "a@b.c" + "x" * 120, "senha": SENHA}, "email"),       # mais de 120
        ({"senha": SENHA}, "email"),
        ({"email": EMAIL}, "senha"),
    ],
)
def test_corpo_invalido_422_no_formato_do_fastapi(cliente, banco, corpo, campo):
    r = cliente.post("/auth/cadastro", json=corpo)

    assert r.status_code == 422
    assert [e["loc"] for e in r.json()["detail"]] == [["body", campo]]
    assert banco.usuarios == {}


def test_senha_no_limite_de_72_bytes_e_aceita(cliente):
    senha = "á" * 36  # 36 caracteres, 72 bytes
    assert cliente.post("/auth/cadastro", json={"email": EMAIL, "senha": senha}).status_code == 201
    assert cliente.post("/auth/login", json={"email": EMAIL, "senha": senha}).status_code == 200


def test_cadastro_e_publico_e_documentado_no_swagger(cliente):
    contrato = cliente.get("/openapi.json").json()
    rota = contrato["paths"]["/auth/cadastro"]["post"]

    assert "security" not in rota
    assert rota["tags"] == ["autenticação"]
    assert {"201", "409", "422"} <= set(rota["responses"])
    for schema in ("Cadastro", "UsuarioSaida"):
        assert contrato["components"]["schemas"][schema].get("examples"), schema
    assert "senha_hash" not in contrato["components"]["schemas"]["UsuarioSaida"]["properties"]


class ErroDeBanco(Exception):
    """Faz o papel do OperationalError (o HTTP não conhece o SQLAlchemy)."""


class CadastroComBancoFora(CadastrarUsuario):
    def __init__(self) -> None:
        pass

    def executar(self, email, senha):
        raise ErroDeBanco()


def test_banco_fora_503(banco, saude):
    app = criar_app_fake(
        montar_componentes(banco, saude, cadastrar_usuario=CadastroComBancoFora()),
        erros_indisponibilidade=(ErroDeBanco,),
    )
    r = TestClient(app).post("/auth/cadastro", json={"email": EMAIL, "senha": SENHA})

    assert r.status_code == 503
    assert r.headers["retry-after"] == "5"
