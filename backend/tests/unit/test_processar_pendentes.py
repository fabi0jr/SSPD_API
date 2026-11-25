from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.aplicacao.processar_pendentes import ProcessarPendentes
from app.dominio.entidades import (
    Alerta,
    Leitura,
    Paciente,
    ResultadoCiclo,
    Severidade,
    StatusAlerta,
    TipoAlerta,
)
from tests.fakes import (
    BancoFake,
    ErroInjetado,
    FilaPendentesFake,
    RepositorioLeiturasFake,
    RepositorioPacientesFake,
)

T0 = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def banco() -> BancoFake:
    banco = BancoFake()
    pacientes = RepositorioPacientesFake(banco)
    for pid in ("P001", "P002"):
        pacientes.criar(Paciente(id=pid, nome=f"Paciente {pid}"))
    return banco


@pytest.fixture
def fila(banco) -> FilaPendentesFake:
    return FilaPendentesFake(banco)


@pytest.fixture
def caso_de_uso(fila) -> ProcessarPendentes:
    return ProcessarPendentes(fila)


def gravar(banco: BancoFake, segundos: int = 0, paciente_id: str = "P001", **kwargs) -> Leitura:
    dados = {"fc": 80, "spo2": 97.0, "temp": 36.5}
    dados.update(kwargs)
    l = Leitura(id=uuid4(), paciente_id=paciente_id, ts=T0 + timedelta(seconds=segundos), **dados)
    RepositorioLeiturasFake(banco).salvar_se_nova(l)
    return l


def alerta_aberto(banco: BancoFake, paciente_id: str, tipo: TipoAlerta, status=StatusAlerta.ABERTO) -> None:
    leitura = gravar(banco, paciente_id=paciente_id)
    banco.leituras[leitura.id] = replace(banco.leituras[leitura.id], processada=True)
    banco.inserir_alerta(
        Alerta(paciente_id, leitura.id, tipo, valor=85.0, severidade=Severidade.ALTA, status=status)
    )


def alertas(banco: BancoFake) -> list[tuple[str, TipoAlerta]]:
    return sorted((a.paciente_id, a.tipo) for a in banco.alertas.values())


# --- Fluxo básico ----------------------------------------------------------------

def test_sem_pendentes(caso_de_uso):
    assert caso_de_uso.executar(500) == ResultadoCiclo(processadas=0, alertas_criados=0, backlog=0)


def test_leituras_normais_sao_marcadas_sem_alerta(caso_de_uso, banco):
    for i in range(3):
        gravar(banco, i)

    assert caso_de_uso.executar(500) == ResultadoCiclo(processadas=3, alertas_criados=0, backlog=0)
    assert banco.pendentes() == []
    assert banco.alertas == {}


def test_leitura_anomala_gera_alerta_com_os_dados_da_leitura(caso_de_uso, banco):
    l = gravar(banco, spo2=86.0)

    resultado = caso_de_uso.executar(500)

    assert resultado == ResultadoCiclo(processadas=1, alertas_criados=1, backlog=0)
    [alerta] = banco.alertas.values()
    assert (alerta.paciente_id, alerta.leitura_id, alerta.tipo) == ("P001", l.id, TipoAlerta.SPO2_BAIXA)
    assert alerta.valor == 86.0
    assert alerta.status == StatusAlerta.ABERTO
    assert alerta.id is not None


def test_leitura_com_varias_anomalias_gera_todos_os_alertas(caso_de_uso, banco):
    gravar(banco, fc=130, spo2=85.0, temp=39.0)

    assert caso_de_uso.executar(500).alertas_criados == 3
    assert alertas(banco) == [
        ("P001", TipoAlerta.FEBRE),
        ("P001", TipoAlerta.SPO2_BAIXA),
        ("P001", TipoAlerta.TAQUICARDIA),
    ]


# --- Anti-spam -------------------------------------------------------------------

def test_nao_duplica_alerta_ja_aberto_no_banco(caso_de_uso, banco):
    alerta_aberto(banco, "P001", TipoAlerta.SPO2_BAIXA)
    gravar(banco, 10, spo2=85.0)

    resultado = caso_de_uso.executar(500)

    assert resultado == ResultadoCiclo(processadas=1, alertas_criados=0, backlog=0)
    assert len(banco.alertas) == 1


def test_alerta_reconhecido_nao_bloqueia_novo(caso_de_uso, banco):
    alerta_aberto(banco, "P001", TipoAlerta.SPO2_BAIXA, status=StatusAlerta.RECONHECIDO)
    gravar(banco, 10, spo2=85.0)

    assert caso_de_uso.executar(500).alertas_criados == 1


def test_alerta_aberto_de_outro_tipo_nao_bloqueia(caso_de_uso, banco):
    alerta_aberto(banco, "P001", TipoAlerta.FEBRE)
    gravar(banco, 10, spo2=85.0)

    assert caso_de_uso.executar(500).alertas_criados == 1


def test_nao_duplica_dentro_do_mesmo_lote_e_usa_a_leitura_mais_antiga(caso_de_uso, banco):
    # Gravadas fora de ordem: o lote precisa vir ordenado por ts.
    depois = gravar(banco, 20, spo2=84.0)
    antes = gravar(banco, 10, spo2=86.0)

    resultado = caso_de_uso.executar(500)

    assert resultado == ResultadoCiclo(processadas=2, alertas_criados=1, backlog=0)
    [alerta] = banco.alertas.values()
    assert alerta.leitura_id == antes.id
    assert alerta.leitura_id != depois.id


def test_mesmo_tipo_em_pacientes_diferentes_gera_um_alerta_para_cada(caso_de_uso, banco):
    gravar(banco, 0, "P001", spo2=85.0)
    gravar(banco, 1, "P002", spo2=85.0)
    alerta_aberto(banco, "P002", TipoAlerta.FEBRE)  # não interfere no SPO2 do P002

    assert caso_de_uso.executar(500).alertas_criados == 2
    assert ("P001", TipoAlerta.SPO2_BAIXA) in alertas(banco)
    assert ("P002", TipoAlerta.SPO2_BAIXA) in alertas(banco)


# --- Lote e backlog --------------------------------------------------------------

def test_respeita_o_limite_e_informa_o_backlog(caso_de_uso, banco):
    for i in range(5):
        gravar(banco, i)

    assert caso_de_uso.executar(2) == ResultadoCiclo(processadas=2, alertas_criados=0, backlog=3)
    assert caso_de_uso.executar(2) == ResultadoCiclo(processadas=2, alertas_criados=0, backlog=1)
    assert caso_de_uso.executar(2) == ResultadoCiclo(processadas=1, alertas_criados=0, backlog=0)


def test_processa_as_mais_antigas_primeiro(caso_de_uso, banco):
    nova = gravar(banco, 100)
    antiga = gravar(banco, 0)

    caso_de_uso.executar(1)

    assert banco.leituras[antiga.id].processada is True
    assert banco.leituras[nova.id].processada is False


# --- Falha no meio do ciclo ------------------------------------------------------

@pytest.mark.parametrize("operacao", ["tipos_abertos", "registrar_alertas", "marcar_processadas"])
def test_falha_no_meio_faz_rollback_e_o_lote_e_reprocessado(caso_de_uso, fila, banco, operacao):
    gravar(banco, 0, spo2=85.0)
    gravar(banco, 1)
    fila.falhar_em = operacao

    with pytest.raises(ErroInjetado):
        caso_de_uso.executar(500)

    assert fila.rollbacks == 1
    assert len(banco.pendentes()) == 2
    assert banco.alertas == {}

    fila.falhar_em = None  # o banco voltou: o próximo ciclo processa o mesmo lote
    assert caso_de_uso.executar(500) == ResultadoCiclo(processadas=2, alertas_criados=1, backlog=0)
