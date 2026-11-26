"""FilaPendentesPg: o contrato de transação que sustenta a tolerância a falhas do worker."""

import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.adaptadores.saida.postgres.db import criar_engine, criar_fabrica_sessoes
from app.adaptadores.saida.postgres.fila_pendentes import FilaPendentesPg
from app.adaptadores.saida.postgres.models import AlertaModel
from app.adaptadores.saida.postgres.repositorios import RepositorioAlertasPg, RepositorioLeiturasPg
from app.aplicacao.processar_pendentes import ProcessarPendentes
from app.dominio.entidades import Alerta, Leitura, ResultadoCiclo, Severidade, StatusAlerta, TipoAlerta

T0 = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


def gravar(sessoes, segundos: int = 0, paciente_id: str = "P001", spo2: float = 97.0) -> Leitura:
    l = Leitura(id=uuid4(), paciente_id=paciente_id, fc=80, spo2=spo2, temp=36.5,
                ts=T0 + timedelta(seconds=segundos))
    RepositorioLeiturasPg(sessoes).salvar_se_nova(l)
    return l


def spo2_baixa(l: Leitura) -> Alerta:
    return Alerta(l.paciente_id, l.id, TipoAlerta.SPO2_BAIXA, l.spo2, Severidade.ALTA)


# --- Commit / rollback -----------------------------------------------------------

def test_saida_normal_faz_commit(sessoes, pacientes):
    l = gravar(sessoes, spo2=85.0)
    fila = FilaPendentesPg(sessoes)

    with fila.reservar(10) as lote:
        assert [x.id for x in lote.leituras] == [l.id]
        lote.registrar_alertas([spo2_baixa(l)])
        assert lote.tipos_abertos("P001") == {TipoAlerta.SPO2_BAIXA}  # visível na própria transação
        assert RepositorioAlertasPg(sessoes).listar(None, None, 10) == []  # invisível fora dela
        lote.marcar_processadas()

    assert fila.contar_pendentes() == 0
    assert len(RepositorioAlertasPg(sessoes).listar(None, None, 10)) == 1


def test_excecao_dentro_do_with_faz_rollback(sessoes, pacientes):
    l = gravar(sessoes, spo2=85.0)
    fila = FilaPendentesPg(sessoes)

    with pytest.raises(RuntimeError):
        with fila.reservar(10) as lote:
            lote.registrar_alertas([spo2_baixa(l)])
            lote.marcar_processadas()
            raise RuntimeError("worker caiu no meio do ciclo")

    assert fila.contar_pendentes() == 1
    assert RepositorioAlertasPg(sessoes).listar(None, None, 10) == []
    with fila.reservar(10) as lote:  # as travas foram liberadas
        assert [x.id for x in lote.leituras] == [l.id]


def test_reserva_na_ordem_de_ts_e_respeita_o_limite(sessoes, pacientes):
    nova = gravar(sessoes, 100)
    antiga = gravar(sessoes, 0)
    meio = gravar(sessoes, 50)

    with FilaPendentesPg(sessoes).reservar(2) as lote:
        assert [l.id for l in lote.leituras] == [antiga.id, meio.id]
    assert nova  # fica para o próximo lote


def test_lote_vazio(sessoes, pacientes):
    fila = FilaPendentesPg(sessoes)
    with fila.reservar(10) as lote:
        assert lote.leituras == []
        lote.registrar_alertas([])
        lote.marcar_processadas()
    assert fila.contar_pendentes() == 0


# --- Concorrência (SKIP LOCKED) --------------------------------------------------

def test_duas_reservas_simultaneas_nao_pegam_a_mesma_leitura(sessoes, pacientes, engine):
    for s in range(3):
        gravar(sessoes, s)
    # Sem SKIP LOCKED a segunda reserva esperaria a primeira para sempre (mesma
    # thread). O lock_timeout transforma essa espera em erro, em vez de pendurar o CI.
    engine_curto = criar_engine(engine.url)
    event.listen(engine_curto, "connect", lambda con, _: con.execute("SET lock_timeout = '2s'"))
    fila = FilaPendentesPg(criar_fabrica_sessoes(engine_curto))

    with fila.reservar(2) as lote_a:
        with fila.reservar(10) as lote_b:  # outra conexão, enquanto A ainda segura as travas
            ids_b = {l.id for l in lote_b.leituras}
        ids_a = {l.id for l in lote_a.leituras}

    engine_curto.dispose()
    assert len(ids_a) == 2
    assert len(ids_b) == 1
    assert ids_a.isdisjoint(ids_b)


def test_varios_workers_concorrentes_processam_cada_leitura_uma_vez(sessoes, pacientes):
    total = 300
    for s in range(total):
        gravar(sessoes, s, paciente_id="P001" if s % 2 else "P002")

    fila = FilaPendentesPg(sessoes)
    vistas: list = []
    trava = threading.Lock()
    erros: list[BaseException] = []

    def worker():
        try:
            while True:
                with fila.reservar(25) as lote:
                    if not lote.leituras:
                        return
                    with trava:
                        vistas.extend(l.id for l in lote.leituras)
                    time.sleep(0.01)  # segura as travas para forçar sobreposição
                    lote.marcar_processadas()
        except BaseException as erro:  # noqa: BLE001 - repassado ao teste
            erros.append(erro)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert erros == []
    assert len(vistas) == total
    assert len(set(vistas)) == total  # nenhuma leitura em dois lotes
    assert fila.contar_pendentes() == 0


def test_indice_unico_impede_dois_alertas_abertos_do_mesmo_tipo(sessoes, pacientes):
    """Última linha de defesa do anti-spam, para o caso de dois workers concorrentes."""
    l1, l2 = gravar(sessoes, 0, spo2=85.0), gravar(sessoes, 1, spo2=84.0)
    fila = FilaPendentesPg(sessoes)

    with fila.reservar(1) as lote:
        lote.registrar_alertas([spo2_baixa(l1)])
    with fila.reservar(1) as lote:
        lote.registrar_alertas([spo2_baixa(l2)])  # ignorado, sem derrubar o lote

    alertas = RepositorioAlertasPg(sessoes)
    assert [a.leitura_id for a in alertas.listar(StatusAlerta.ABERTO, "P001", 10)] == [l1.id]

    # Depois de reconhecido, um novo alerta do mesmo tipo pode ser aberto.
    [aberto] = alertas.listar(StatusAlerta.ABERTO, "P001", 10)
    alertas.atualizar_status(aberto.id, StatusAlerta.RECONHECIDO)
    with fila.reservar(1) as lote:
        lote.registrar_alertas([spo2_baixa(l2)])
    assert len(alertas.listar(StatusAlerta.ABERTO, "P001", 10)) == 1


# --- Caso de uso de ponta a ponta com o adaptador real ---------------------------

def test_processar_pendentes_com_postgres(sessoes, pacientes):
    gravar(sessoes, 0, "P001", spo2=86.0)
    gravar(sessoes, 1, "P001", spo2=85.0)   # anti-spam dentro do lote
    gravar(sessoes, 2, "P002", spo2=97.0)
    gravar(sessoes, 3, "P002", spo2=97.0)
    caso_de_uso = ProcessarPendentes(FilaPendentesPg(sessoes))

    assert caso_de_uso.executar(3) == ResultadoCiclo(processadas=3, alertas_criados=1, backlog=1)
    assert caso_de_uso.executar(3) == ResultadoCiclo(processadas=1, alertas_criados=0, backlog=0)

    gravar(sessoes, 10, "P001", spo2=80.0)   # anti-spam contra o alerta já aberto no banco
    assert caso_de_uso.executar(3) == ResultadoCiclo(processadas=1, alertas_criados=0, backlog=0)

    with sessoes() as s:
        assert s.scalars(select(AlertaModel.tipo)).all() == ["SPO2_BAIXA"]
