"""Consultas, CadastrarPaciente e Autenticar (casos de uso do dashboard)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
import pytest

from app.adaptadores.entrada.http.seguranca import verificar_senha
from app.aplicacao.autenticar import Autenticar
from app.aplicacao.cadastrar_paciente import CadastrarPaciente
from app.aplicacao.consultas import LIMITE_MAXIMO, Consultas
from app.dominio.entidades import Alerta, Leitura, Paciente, Severidade, StatusAlerta, TipoAlerta, Usuario
from app.dominio.erros import CredenciaisInvalidas, PacienteJaExiste, PacienteNaoEncontrado
from tests.fakes import (
    RepositorioAlertasFake,
    RepositorioLeiturasFake,
    RepositorioPacientesFake,
    RepositorioUsuariosFake,
)

T0 = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def consultas(banco) -> Consultas:
    pacientes = RepositorioPacientesFake(banco)
    pacientes.criar(Paciente(id="P001", nome="Paciente 1"))
    pacientes.criar(Paciente(id="P002", nome="Paciente 2"))
    return Consultas(pacientes, RepositorioLeiturasFake(banco), RepositorioAlertasFake(banco))


def gravar_leituras(banco, paciente_id: str, n: int) -> list[Leitura]:
    leituras = [
        Leitura(uuid4(), paciente_id, 80, 97.0, 36.5, T0 + timedelta(minutes=i)) for i in range(n)
    ]
    repo = RepositorioLeiturasFake(banco)
    for l in leituras:
        repo.salvar_se_nova(l)
    return leituras


# --- Consultas -------------------------------------------------------------------

def test_ultima_leitura(banco, consultas):
    leituras = gravar_leituras(banco, "P001", 3)
    assert consultas.ultima_leitura("P001").id == leituras[-1].id


def test_ultima_leitura_de_paciente_sem_leituras_e_none(consultas):
    assert consultas.ultima_leitura("P002") is None


@pytest.mark.parametrize("metodo", ["ultima_leitura", "historico"])
def test_paciente_inexistente_404_em_vez_de_vazio(consultas, metodo):
    with pytest.raises(PacienteNaoEncontrado):
        getattr(consultas, metodo)("P999")


def test_historico_ordenado_e_limitado(banco, consultas):
    leituras = gravar_leituras(banco, "P001", 5)
    resultado = consultas.historico("P001", limite=3)
    assert [l.id for l in resultado] == [l.id for l in reversed(leituras)][:3]


def test_historico_filtra_por_intervalo_inclusivo(banco, consultas):
    leituras = gravar_leituras(banco, "P001", 5)
    resultado = consultas.historico("P001", desde=leituras[1].ts, ate=leituras[3].ts)
    assert [l.id for l in resultado] == [leituras[3].id, leituras[2].id, leituras[1].id]


def test_limite_e_cortado_no_maximo(banco, consultas):
    gravar_leituras(banco, "P001", 2)

    class Espiao(RepositorioLeiturasFake):
        limite_recebido = None

        def historico(self, paciente_id, desde, ate, limite):
            Espiao.limite_recebido = limite
            return super().historico(paciente_id, desde, ate, limite)

    c = Consultas(RepositorioPacientesFake(banco), Espiao(banco), RepositorioAlertasFake(banco))
    c.historico("P001", limite=LIMITE_MAXIMO * 10)
    assert Espiao.limite_recebido == LIMITE_MAXIMO


def test_listar_pacientes_com_resumo(banco, consultas):
    leituras = gravar_leituras(banco, "P001", 2)
    banco.inserir_alerta(Alerta("P001", leituras[0].id, TipoAlerta.FEBRE, 38.5, Severidade.MEDIA))

    p1, p2 = consultas.listar_pacientes()
    assert (p1.paciente.id, p1.ultima_leitura.id, p1.alertas_abertos) == ("P001", leituras[-1].id, 1)
    assert (p2.paciente.id, p2.ultima_leitura, p2.alertas_abertos) == ("P002", None, 0)


def test_listar_alertas_filtra_por_status_e_paciente(banco, consultas):
    l1 = gravar_leituras(banco, "P001", 1)[0]
    l2 = gravar_leituras(banco, "P002", 1)[0]
    aberto = banco.inserir_alerta(Alerta("P001", l1.id, TipoAlerta.FEBRE, 38.5, Severidade.MEDIA))
    banco.inserir_alerta(Alerta("P001", l1.id, TipoAlerta.TAQUICARDIA, 130, Severidade.MEDIA,
                                status=StatusAlerta.RECONHECIDO))
    banco.inserir_alerta(Alerta("P002", l2.id, TipoAlerta.FEBRE, 39.0, Severidade.MEDIA))

    assert len(consultas.listar_alertas()) == 3
    assert [a.id for a in consultas.listar_alertas(StatusAlerta.ABERTO, "P001")] == [aberto.id]
    assert consultas.listar_alertas(paciente_id="P999") == []


# --- CadastrarPaciente -----------------------------------------------------------

def test_cadastrar_paciente_devolve_o_gravado(banco):
    gravado = CadastrarPaciente(RepositorioPacientesFake(banco)).executar(Paciente("P010", "Novo", "L-10"))
    assert (gravado.id, gravado.leito) == ("P010", "L-10")
    assert gravado.criado_em is not None


def test_cadastrar_paciente_repetido(banco):
    caso_de_uso = CadastrarPaciente(RepositorioPacientesFake(banco))
    caso_de_uso.executar(Paciente("P010", "Novo"))
    with pytest.raises(PacienteJaExiste):
        caso_de_uso.executar(Paciente("P010", "Outro"))


# --- Autenticar ------------------------------------------------------------------

@pytest.fixture
def autenticar(banco) -> Autenticar:
    # rounds=4: o mínimo do bcrypt, para o teste ser rápido.
    senha_hash = bcrypt.hashpw(b"senha-certa", bcrypt.gensalt(rounds=4)).decode()
    banco.usuarios["admin@example.com"] = Usuario(1, "admin@example.com", senha_hash)
    return Autenticar(RepositorioUsuariosFake(banco), verificar_senha)


def test_login_correto_devolve_o_usuario(autenticar):
    assert autenticar.executar("admin@example.com", "senha-certa").id == 1


@pytest.mark.parametrize(
    ("email", "senha"),
    [("admin@example.com", "senha-errada"), ("ninguem@example.com", "senha-certa"), ("admin@example.com", "")],
)
def test_login_errado(autenticar, email, senha):
    with pytest.raises(CredenciaisInvalidas) as erro:
        autenticar.executar(email, senha)
    assert str(erro.value) == "e-mail ou senha inválidos"


def test_email_inexistente_tambem_roda_o_bcrypt(banco):
    chamadas = []

    def verificar(senha, senha_hash):
        chamadas.append(senha_hash)
        return False

    with pytest.raises(CredenciaisInvalidas):
        Autenticar(RepositorioUsuariosFake(banco), verificar).executar("ninguem@example.com", "x")
    assert chamadas == [None]  # a verificação acontece mesmo sem usuário


def test_verificar_senha():
    senha_hash = bcrypt.hashpw(b"abc", bcrypt.gensalt(rounds=4)).decode()
    assert verificar_senha("abc", senha_hash) is True
    assert verificar_senha("abd", senha_hash) is False
    assert verificar_senha("abc", None) is False
    assert verificar_senha("abc", "hash-corrompido") is False
