"""Adaptador de entrada do worker: loop, logs, falhas e SIGTERM."""

import logging
import signal
import threading
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app import composicao, config
from app.adaptadores.entrada.worker.main import Worker, instalar_sinais
from app.aplicacao.processar_pendentes import ProcessarPendentes
from app.dominio.entidades import Leitura, Paciente, ResultadoCiclo
from tests.fakes import FilaPendentesFake

T0 = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


class EventoFake:
    """threading.Event sem dormir: registra cada espera do loop."""

    def __init__(self) -> None:
        self.esperas: list[float] = []
        self._acionado = False

    def is_set(self) -> bool:
        return self._acionado

    def set(self) -> None:
        self._acionado = True

    def wait(self, timeout: float | None = None) -> bool:
        self.esperas.append(timeout)
        return self._acionado


class CicloRoteirizado:
    """Caso de uso falso: devolve (ou levanta) as respostas em ordem e, na
    última, aciona a parada, como um SIGTERM chegando no meio do ciclo."""

    def __init__(self, parar: EventoFake, *respostas: ResultadoCiclo | Exception) -> None:
        self._parar = parar
        self._respostas = list(respostas)
        self.limites: list[int] = []

    def executar(self, limite: int) -> ResultadoCiclo:
        self.limites.append(limite)
        resposta = self._respostas.pop(0)
        if not self._respostas:
            self._parar.set()
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


class BancoFora(Exception):
    """Faz o papel do OperationalError do SQLAlchemy."""


def r(processadas: int, alertas: int = 0, backlog: int = 0) -> ResultadoCiclo:
    return ResultadoCiclo(processadas=processadas, alertas_criados=alertas, backlog=backlog)


def worker_roteirizado(*respostas, lote: int = 10, intervalo: float = 2):
    parar = EventoFake()
    caso = CicloRoteirizado(parar, *respostas)
    worker = Worker(caso, intervalo=intervalo, lote=lote, parar=parar,
                    erros_indisponibilidade=(BancoFora,))
    return worker, caso, parar


def mensagens(caplog, nivel: int) -> list[str]:
    return [rec.getMessage() for rec in caplog.records if rec.name == "worker" and rec.levelno == nivel]


# --- Ciclo e logs -----------------------------------------------------------------

def test_ciclo_com_o_caso_de_uso_real_gera_alerta_e_loga(banco, caplog):
    banco.pacientes["P001"] = Paciente(id="P001", nome="Paciente P001")
    for i, spo2 in enumerate((97.0, 85.0, 84.0)):  # duas leituras de SpO2 baixa: 1 alerta (anti-spam)
        l = Leitura(id=uuid4(), paciente_id="P001", fc=80, spo2=spo2, temp=36.5, ts=T0)
        banco.leituras[l.id] = l
    parar = EventoFake()
    worker = Worker(ProcessarPendentes(FilaPendentesFake(banco)), intervalo=2, lote=500, parar=parar)

    with caplog.at_level(logging.INFO, logger="worker"):
        resultado = worker.ciclo()

    assert resultado == r(processadas=3, alertas=1, backlog=0)
    assert len(banco.alertas) == 1
    [linha] = [m for m in mensagens(caplog, logging.INFO) if m.startswith("ciclo:")]
    assert linha.startswith("ciclo: processadas=3 alertas=1 backlog=0 duracao_ms=")


def test_ciclo_sem_pendentes_nao_loga(caplog):
    worker, _, _ = worker_roteirizado(r(0))

    with caplog.at_level(logging.INFO, logger="worker"):
        resultado = worker.ciclo()

    assert resultado == r(0)
    assert mensagens(caplog, logging.INFO) == []


def test_loop_usa_o_lote_configurado_e_espera_o_intervalo(caplog):
    worker, caso, parar = worker_roteirizado(r(3), r(0), lote=500, intervalo=2)

    with caplog.at_level(logging.INFO, logger="worker"):
        worker.rodar()

    assert caso.limites == [500, 500]
    assert parar.esperas == [2, 2]
    infos = mensagens(caplog, logging.INFO)
    assert infos[0] == "worker iniciado (intervalo=2s, lote=500)"
    assert [m.split(" duracao_ms")[0] for m in infos[1:-1]] == ["ciclo: processadas=3 alertas=0 backlog=0"]
    assert infos[-1] == "worker encerrado"


def test_lote_cheio_com_backlog_segue_sem_esperar():
    # Volta de uma queda: 25 pendentes, lote de 10. Só espera quando esvazia.
    worker, caso, parar = worker_roteirizado(r(10, backlog=15), r(10, backlog=5), r(5), lote=10)

    worker.rodar()

    assert len(caso.limites) == 3
    assert parar.esperas == [2]


def test_lote_cheio_sem_backlog_espera():
    worker, _, parar = worker_roteirizado(r(10, backlog=0), r(0), lote=10)

    worker.rodar()

    assert parar.esperas == [2, 2]


# --- Falhas -----------------------------------------------------------------------

def test_banco_fora_loga_uma_linha_sem_traceback_e_tenta_de_novo(caplog):
    worker, caso, parar = worker_roteirizado(BancoFora("conexão recusada"), r(4))

    with caplog.at_level(logging.INFO, logger="worker"):
        worker.rodar()

    assert len(caso.limites) == 2  # não morreu: tentou de novo no ciclo seguinte
    assert parar.esperas == [2, 2]
    [aviso] = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert aviso.getMessage() == "ciclo: banco indisponível (BancoFora); nova tentativa em 2s"
    assert aviso.exc_info is None
    infos = mensagens(caplog, logging.INFO)
    assert infos[1] == "ciclo: banco disponível novamente"
    assert infos[2].startswith("ciclo: processadas=4")


def test_volta_do_banco_aparece_no_log_mesmo_sem_pendentes(caplog):
    worker, _, _ = worker_roteirizado(BancoFora("fora"), BancoFora("fora"), r(0), r(0))

    with caplog.at_level(logging.INFO, logger="worker"):
        worker.rodar()

    infos = mensagens(caplog, logging.INFO)
    assert infos.count("ciclo: banco disponível novamente") == 1  # só na primeira volta
    assert not any(m.startswith("ciclo: processadas=") for m in infos)


def test_erro_inesperado_loga_traceback_e_continua(caplog):
    worker, caso, _ = worker_roteirizado(RuntimeError("bug"), r(1))

    with caplog.at_level(logging.INFO, logger="worker"):
        worker.rodar()

    assert len(caso.limites) == 2
    [erro] = [rec for rec in caplog.records if rec.levelno == logging.ERROR]
    assert erro.getMessage() == "ciclo: erro inesperado; o lote volta para a fila"
    assert erro.exc_info is not None


# --- Parada -----------------------------------------------------------------------

def test_parada_acionada_antes_nao_roda_ciclo():
    worker, caso, parar = worker_roteirizado(r(1))
    parar.set()

    worker.rodar()

    assert caso.limites == []


def test_parada_interrompe_a_espera_sem_aguardar_o_intervalo():
    """Com threading.Event de verdade e intervalo de 60 s, sair leva milissegundos."""
    parar = threading.Event()
    caso = CicloRoteirizado(EventoFake(), r(0), r(0))  # não aciona `parar` sozinho
    worker = Worker(caso, intervalo=60, lote=10, parar=parar)
    thread = threading.Thread(target=worker.rodar)
    thread.start()

    parar.set()
    thread.join(timeout=2)

    assert not thread.is_alive()


@pytest.mark.parametrize("sinal", [signal.SIGTERM, signal.SIGINT])
def test_sinal_aciona_a_parada(sinal, caplog):
    anteriores = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    parar = threading.Event()
    try:
        instalar_sinais(parar)
        with caplog.at_level(logging.INFO, logger="worker"):
            signal.raise_signal(sinal)
    finally:
        for s, handler in anteriores.items():
            signal.signal(s, handler)

    assert parar.is_set()
    assert mensagens(caplog, logging.INFO) == [
        f"{sinal.name} recebido; encerrando depois do ciclo atual"
    ]


# --- Configuração -----------------------------------------------------------------

@pytest.mark.parametrize("intervalo, lote", [(0, 10), (-1, 10), (2, 0), (2, -5)])
def test_intervalo_ou_lote_invalido(intervalo, lote):
    with pytest.raises(ValueError):
        Worker(CicloRoteirizado(EventoFake(), r(0)), intervalo=intervalo, lote=lote)


@pytest.mark.parametrize("variavel, valor", [("WORKER_LOTE", "0"), ("WORKER_INTERVALO", "abc")])
def test_rodar_worker_recusa_configuracao_invalida(monkeypatch, variavel, valor):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@localhost:1/nada")  # não conecta
    monkeypatch.setenv(variavel, valor)

    with pytest.raises(config.ConfiguracaoAusente, match=variavel):
        composicao.rodar_worker()


def test_rodar_worker_exige_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(config.ConfiguracaoAusente, match="DATABASE_URL"):
        composicao.rodar_worker()
