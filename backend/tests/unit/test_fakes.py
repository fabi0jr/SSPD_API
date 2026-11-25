"""Os fakes precisam cumprir o contrato das portas, senão os testes dos casos de uso mentem.

Os mesmos comportamentos são verificados no adaptador Postgres, nos testes de integração.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.dominio.entidades import Alerta, Leitura, Paciente, Severidade, StatusAlerta, TipoAlerta
from app.dominio.erros import PacienteJaExiste
from tests.fakes import (
    BancoFake,
    FilaPendentesFake,
    RepositorioAlertasFake,
    RepositorioLeiturasFake,
    RepositorioPacientesFake,
)

T0 = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


def leitura(segundos: int = 0, paciente_id: str = "P001", spo2: float = 97.0) -> Leitura:
    return Leitura(
        id=uuid4(), paciente_id=paciente_id, fc=80, spo2=spo2, temp=36.5,
        ts=T0 + timedelta(seconds=segundos),
    )


@pytest.fixture
def banco() -> BancoFake:
    banco = BancoFake()
    RepositorioPacientesFake(banco).criar(Paciente(id="P001", nome="Paciente 1"))
    return banco


# --- Repositórios ----------------------------------------------------------------

def test_salvar_se_nova_e_idempotente(banco):
    repo = RepositorioLeiturasFake(banco)
    l = leitura()
    assert repo.salvar_se_nova(l) is True
    assert repo.salvar_se_nova(l) is False
    assert len(banco.leituras) == 1
    assert banco.leituras[l.id].recebido_em is not None


def test_historico_ordenado_por_ts_desc_com_limite_e_janela(banco):
    repo = RepositorioLeiturasFake(banco)
    ls = [leitura(s) for s in (30, 10, 20, 0)]
    for l in ls:
        repo.salvar_se_nova(l)

    assert [l.ts for l in repo.historico("P001", None, None, 10)] == [
        T0 + timedelta(seconds=s) for s in (30, 20, 10, 0)
    ]
    assert len(repo.historico("P001", None, None, 2)) == 2
    janela = repo.historico("P001", T0 + timedelta(seconds=10), T0 + timedelta(seconds=20), 10)
    assert [l.ts for l in janela] == [T0 + timedelta(seconds=20), T0 + timedelta(seconds=10)]
    assert repo.ultima("P001").ts == T0 + timedelta(seconds=30)
    assert repo.ultima("P999") is None


def test_criar_paciente_repetido(banco):
    with pytest.raises(PacienteJaExiste):
        RepositorioPacientesFake(banco).criar(Paciente(id="P001", nome="Outro"))


def test_listar_com_resumo(banco):
    pacientes = RepositorioPacientesFake(banco)
    pacientes.criar(Paciente(id="P002", nome="Paciente 2"))
    recente = leitura(10)
    RepositorioLeiturasFake(banco).salvar_se_nova(leitura(0))
    RepositorioLeiturasFake(banco).salvar_se_nova(recente)
    banco.inserir_alerta(Alerta("P001", recente.id, TipoAlerta.FEBRE, 38.5, Severidade.MEDIA))
    banco.inserir_alerta(
        Alerta("P001", recente.id, TipoAlerta.SPO2_BAIXA, 85.0, Severidade.ALTA, StatusAlerta.RECONHECIDO)
    )

    [p1, p2] = pacientes.listar_com_resumo()

    assert (p1.paciente.id, p1.ultima_leitura.id, p1.alertas_abertos) == ("P001", recente.id, 1)
    assert (p2.paciente.id, p2.ultima_leitura, p2.alertas_abertos) == ("P002", None, 0)


def test_alertas_listar_filtra_e_obter_devolve_copia(banco):
    l = leitura()
    RepositorioLeiturasFake(banco).salvar_se_nova(l)
    a1 = banco.inserir_alerta(Alerta("P001", l.id, TipoAlerta.FEBRE, 38.5, Severidade.MEDIA))
    banco.inserir_alerta(Alerta("P001", l.id, TipoAlerta.SPO2_BAIXA, 85.0, Severidade.ALTA))
    repo = RepositorioAlertasFake(banco)

    repo.atualizar_status(a1.id, StatusAlerta.RECONHECIDO)
    repo.atualizar_status(999, StatusAlerta.RECONHECIDO)  # inexistente: sem erro

    assert [a.tipo for a in repo.listar(StatusAlerta.ABERTO, None, 10)] == [TipoAlerta.SPO2_BAIXA]
    assert len(repo.listar(None, "P001", 10)) == 2
    assert repo.listar(None, "P999", 10) == []

    copia = repo.obter(a1.id)
    copia.status = StatusAlerta.ABERTO
    assert repo.obter(a1.id).status == StatusAlerta.RECONHECIDO
    assert repo.obter(999) is None


# --- Fila de pendentes -----------------------------------------------------------

def test_reservar_commit_aplica_as_mudancas(banco):
    l = leitura(spo2=85.0)
    RepositorioLeiturasFake(banco).salvar_se_nova(l)
    fila = FilaPendentesFake(banco)

    with fila.reservar(10) as lote:
        assert [x.id for x in lote.leituras] == [l.id]
        lote.registrar_alertas([Alerta("P001", l.id, TipoAlerta.SPO2_BAIXA, 85.0, Severidade.ALTA)])
        assert lote.tipos_abertos("P001") == {TipoAlerta.SPO2_BAIXA}  # visível na transação
        assert banco.alertas == {}  # mas ainda não gravado
        lote.marcar_processadas()

    assert fila.contar_pendentes() == 0
    assert len(banco.alertas) == 1


def test_reservar_excecao_descarta_tudo(banco):
    l = leitura(spo2=85.0)
    RepositorioLeiturasFake(banco).salvar_se_nova(l)
    fila = FilaPendentesFake(banco)

    with pytest.raises(RuntimeError):
        with fila.reservar(10) as lote:
            lote.registrar_alertas([Alerta("P001", l.id, TipoAlerta.SPO2_BAIXA, 85.0, Severidade.ALTA)])
            lote.marcar_processadas()
            raise RuntimeError("worker morreu")

    assert fila.contar_pendentes() == 1
    assert banco.alertas == {}
    with fila.reservar(10) as lote:  # a trava foi liberada
        assert len(lote.leituras) == 1


def test_reservar_sem_marcar_mantem_pendente(banco):
    RepositorioLeiturasFake(banco).salvar_se_nova(leitura())
    fila = FilaPendentesFake(banco)
    with fila.reservar(10):
        pass
    assert fila.contar_pendentes() == 1


def test_reservas_simultaneas_nao_pegam_a_mesma_leitura(banco):
    repo = RepositorioLeiturasFake(banco)
    for s in range(3):
        repo.salvar_se_nova(leitura(s))
    fila = FilaPendentesFake(banco)

    with fila.reservar(2) as lote_a, fila.reservar(10) as lote_b:  # SKIP LOCKED
        ids_a = {l.id for l in lote_a.leituras}
        ids_b = {l.id for l in lote_b.leituras}

    assert len(ids_a) == 2
    assert len(ids_b) == 1
    assert ids_a.isdisjoint(ids_b)
