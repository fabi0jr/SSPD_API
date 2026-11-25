"""Worker montado pelo composicao.py, como em produção, com Postgres real."""

import logging
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.adaptadores.saida.postgres.db import criar_engine
from app.adaptadores.saida.postgres.fila_pendentes import FilaPendentesPg
from app.adaptadores.saida.postgres.repositorios import RepositorioAlertasPg, RepositorioLeiturasPg
from app.composicao import montar_worker
from app.dominio.entidades import Leitura, TipoAlerta

T0 = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


def gravar_pendentes(sessoes, n: int, spo2: float = 97.0, a_partir_de: int = 0) -> None:
    repo = RepositorioLeiturasPg(sessoes)
    for i in range(a_partir_de, a_partir_de + n):
        repo.salvar_se_nova(Leitura(id=uuid4(), paciente_id="P001", fc=80, spo2=spo2, temp=36.5,
                                    ts=T0 + timedelta(seconds=i)))


def test_backlog_e_processado_em_lotes_e_o_worker_para(engine, sessoes, pacientes, caplog):
    # Backlog acumulado com o worker parado: 25 leituras, lote de 10.
    gravar_pendentes(sessoes, 24)
    gravar_pendentes(sessoes, 1, spo2=85.0, a_partir_de=24)  # a mais recente: cai no último lote
    worker = montar_worker(engine, intervalo=60, lote=10)
    thread = threading.Thread(target=worker.rodar)

    with caplog.at_level(logging.INFO, logger="worker"):
        thread.start()
        # Lote cheio com backlog: o worker não espera os 60 s entre os lotes.
        for _ in range(100):
            if FilaPendentesPg(sessoes).contar_pendentes() == 0:
                break
            threading.Event().wait(0.05)
        worker.parar.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert FilaPendentesPg(sessoes).contar_pendentes() == 0
    [alerta] = RepositorioAlertasPg(sessoes).listar(None, None, 10)
    assert alerta.tipo == TipoAlerta.SPO2_BAIXA
    ciclos = [rec.getMessage() for rec in caplog.records if rec.getMessage().startswith("ciclo: processadas=")]
    assert [c.split(" duracao_ms")[0] for c in ciclos[:3]] == [
        "ciclo: processadas=10 alertas=0 backlog=15",
        "ciclo: processadas=10 alertas=0 backlog=5",
        "ciclo: processadas=5 alertas=1 backlog=0",
    ]


def test_banco_fora_nao_derruba_o_worker(caplog):
    # Porta sem Postgres: cada ciclo falha com OperationalError.
    engine = criar_engine("postgresql+psycopg://x:y@127.0.0.1:1/nada")
    worker = montar_worker(engine, intervalo=0.05, lote=10)
    thread = threading.Thread(target=worker.rodar)

    with caplog.at_level(logging.INFO, logger="worker"):
        thread.start()
        threading.Event().wait(0.5)
        worker.parar.set()
        thread.join(timeout=5)
    engine.dispose()

    assert not thread.is_alive()
    avisos = [rec for rec in caplog.records if rec.levelno == logging.WARNING and rec.name == "worker"]
    assert len(avisos) >= 2  # tentou mais de uma vez
    assert all(a.getMessage().startswith("ciclo: banco indisponível (OperationalError)") for a in avisos)
    assert all(a.exc_info is None for a in avisos)
