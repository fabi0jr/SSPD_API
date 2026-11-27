import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.adaptadores.saida.postgres.models import LeituraModel
from app.adaptadores.saida.postgres.repositorios import (
    RepositorioAlertasPg,
    RepositorioLeiturasPg,
    RepositorioPacientesPg,
    RepositorioUsuariosPg,
)
from app.dominio.entidades import Alerta, Leitura, Paciente, Severidade, StatusAlerta, TipoAlerta
from app.dominio.erros import PacienteJaExiste, UsuarioJaExiste
from app.seed import gerar_hash_senha, semear
from tests.integracao.apoio import inserir_alerta

T0 = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


def leitura(segundos: int = 0, paciente_id: str = "P001", **kwargs) -> Leitura:
    dados = {"fc": 80, "spo2": 97.4, "temp": 36.7}
    dados.update(kwargs)
    return Leitura(id=uuid4(), paciente_id=paciente_id, ts=T0 + timedelta(seconds=segundos), **dados)


# --- Leituras --------------------------------------------------------------------

def test_salvar_se_nova_duas_vezes_grava_uma_linha(sessoes, pacientes):
    repo = RepositorioLeiturasPg(sessoes)
    l = leitura()

    assert repo.salvar_se_nova(l) is True
    assert repo.salvar_se_nova(l) is False

    with sessoes() as s:
        assert s.scalar(select(func.count()).select_from(LeituraModel)) == 1


def test_leitura_volta_igual_ao_que_foi_gravado(sessoes, pacientes):
    repo = RepositorioLeiturasPg(sessoes)
    l = leitura(fc=82, spo2=97.4, temp=36.7)
    repo.salvar_se_nova(l)

    gravada = repo.ultima("P001")

    assert (gravada.id, gravada.fc, gravada.spo2, gravada.temp, gravada.ts) == (l.id, 82, 97.4, 36.7, l.ts)
    assert gravada.processada is False
    assert gravada.recebido_em is not None
    assert gravada.ts.utcoffset() == timedelta(0)  # sessão em UTC


def test_ts_com_outro_fuso_e_guardado_como_o_mesmo_instante(sessoes, pacientes):
    repo = RepositorioLeiturasPg(sessoes)
    manaus = timezone(timedelta(hours=-4))
    l = Leitura(id=uuid4(), paciente_id="P001", fc=80, spo2=97.0, temp=36.5,
                ts=datetime(2026, 11, 25, 10, 0, tzinfo=manaus))
    repo.salvar_se_nova(l)

    assert repo.ultima("P001").ts == datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


def test_leitura_de_paciente_inexistente_viola_a_fk(sessoes, pacientes):
    with pytest.raises(IntegrityError):
        RepositorioLeiturasPg(sessoes).salvar_se_nova(leitura(paciente_id="P999"))


def test_historico_ordem_limite_e_janela_inclusiva(sessoes, pacientes):
    repo = RepositorioLeiturasPg(sessoes)
    for s in (30, 10, 20, 0):
        repo.salvar_se_nova(leitura(s))
    repo.salvar_se_nova(leitura(40, paciente_id="P002"))

    todas = repo.historico("P001", None, None, 100)
    assert [l.ts for l in todas] == [T0 + timedelta(seconds=s) for s in (30, 20, 10, 0)]
    assert len(repo.historico("P001", None, None, 2)) == 2

    janela = repo.historico("P001", T0 + timedelta(seconds=10), T0 + timedelta(seconds=20), 100)
    assert [l.ts for l in janela] == [T0 + timedelta(seconds=20), T0 + timedelta(seconds=10)]

    assert repo.ultima("P001").ts == T0 + timedelta(seconds=30)
    assert repo.ultima("P999") is None


# --- Pacientes -------------------------------------------------------------------

def test_criar_obter_e_existe(sessoes):
    repo = RepositorioPacientesPg(sessoes)
    repo.criar(Paciente(id="P010", nome="Paciente 10", leito="UTI-01"))

    assert repo.existe("P010") is True
    assert repo.existe("P999") is False
    p = repo.obter("P010")
    assert (p.id, p.nome, p.leito) == ("P010", "Paciente 10", "UTI-01")
    assert p.criado_em is not None
    assert repo.obter("P999") is None


def test_criar_paciente_repetido(sessoes, pacientes):
    with pytest.raises(PacienteJaExiste):
        pacientes.criar(Paciente(id="P001", nome="Outro"))


def test_listar_com_resumo(sessoes, pacientes):
    leituras = RepositorioLeiturasPg(sessoes)
    antiga, recente = leitura(0), leitura(10)
    leituras.salvar_se_nova(antiga)
    leituras.salvar_se_nova(recente)
    alertas = RepositorioAlertasPg(sessoes)
    inserir_alerta(sessoes, Alerta("P001", recente.id, TipoAlerta.FEBRE, 38.5, Severidade.MEDIA))
    reconhecido = inserir_alerta(sessoes, Alerta("P001", recente.id, TipoAlerta.SPO2_BAIXA, 85.0, Severidade.ALTA))
    alertas.atualizar_status(reconhecido, StatusAlerta.RECONHECIDO)

    [p1, p2] = pacientes.listar_com_resumo()

    assert (p1.paciente.id, p1.ultima_leitura.id, p1.alertas_abertos) == ("P001", recente.id, 1)
    assert (p2.paciente.id, p2.ultima_leitura, p2.alertas_abertos) == ("P002", None, 0)


# --- Alertas ---------------------------------------------------------------------

def test_alertas_listar_obter_e_atualizar(sessoes, pacientes):
    l = leitura()
    RepositorioLeiturasPg(sessoes).salvar_se_nova(l)
    id_febre = inserir_alerta(sessoes, Alerta("P001", l.id, TipoAlerta.FEBRE, 38.5, Severidade.MEDIA))
    inserir_alerta(sessoes, Alerta("P001", l.id, TipoAlerta.SPO2_BAIXA, 85.0, Severidade.ALTA))
    repo = RepositorioAlertasPg(sessoes)

    febre = repo.obter(id_febre)
    assert (febre.tipo, febre.valor, febre.severidade, febre.status) == (
        TipoAlerta.FEBRE, 38.5, Severidade.MEDIA, StatusAlerta.ABERTO,
    )
    assert repo.obter(999) is None

    repo.atualizar_status(id_febre, StatusAlerta.RECONHECIDO)
    repo.atualizar_status(id_febre, StatusAlerta.RECONHECIDO)  # idempotente
    repo.atualizar_status(999, StatusAlerta.RECONHECIDO)       # inexistente: sem erro

    assert [a.tipo for a in repo.listar(StatusAlerta.ABERTO, None, 100)] == [TipoAlerta.SPO2_BAIXA]
    assert [a.id for a in repo.listar(None, "P001", 100)] == [2, 1]  # mais recente primeiro
    assert len(repo.listar(None, None, 1)) == 1
    assert repo.listar(None, "P002", 100) == []


# --- Usuários e seed -------------------------------------------------------------

def test_seed_e_idempotente(engine, sessoes):
    assert semear(engine, "admin@example.com", "senha-forte", 5) == (1, 5)
    assert semear(engine, "admin@example.com", "outra-senha", 7) == (0, 2)

    pacientes = RepositorioPacientesPg(sessoes).listar_com_resumo()
    assert [r.paciente.id for r in pacientes] == ["P001", "P002", "P003", "P004", "P005", "P006", "P007"]

    admin = RepositorioUsuariosPg(sessoes).por_email("admin@example.com")
    assert bcrypt.checkpw(b"senha-forte", admin.senha_hash.encode())  # a 2ª rodada não trocou a senha
    assert RepositorioUsuariosPg(sessoes).por_email("ninguem@example.com") is None


def test_hash_de_senha_nao_guarda_a_senha():
    h = gerar_hash_senha("segredo")
    assert "segredo" not in h
    assert h.startswith("$2")


def test_criar_usuario_e_email_repetido(engine, sessoes):
    semear(engine, "admin@example.com", "senha-forte", 0)  # id 1
    repo = RepositorioUsuariosPg(sessoes)

    usuario = repo.criar("fulano@exemplo.com", "hash-1")

    assert (usuario.id, usuario.email, usuario.senha_hash) == (2, "fulano@exemplo.com", "hash-1")
    assert repo.por_email("fulano@exemplo.com") == usuario
    with pytest.raises(UsuarioJaExiste):
        repo.criar("fulano@exemplo.com", "hash-2")
    with pytest.raises(UsuarioJaExiste):
        repo.criar("admin@example.com", "hash-3")
    assert repo.por_email("fulano@exemplo.com").senha_hash == "hash-1"  # não sobrescreveu


def test_cadastros_simultaneos_do_mesmo_email_gravam_um_so(sessoes):
    """Duas réplicas da API ao mesmo tempo: o UNIQUE decide, um cria e o outro recebe o erro."""
    repo = RepositorioUsuariosPg(sessoes)
    largada = threading.Barrier(2)
    resultados: list[str] = []

    def cadastrar(senha_hash: str) -> None:
        largada.wait()
        try:
            repo.criar("fulano@exemplo.com", senha_hash)
            resultados.append("criou")
        except UsuarioJaExiste:
            resultados.append("ja_existe")

    threads = [threading.Thread(target=cadastrar, args=(f"hash-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(resultados) == ["criou", "ja_existe"]
