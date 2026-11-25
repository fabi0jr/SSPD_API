"""Adaptador de entrada do worker de alertas: loop de polling (CLAUDE.md, seção 9).

A cada `intervalo` segundos chama `ProcessarPendentes.executar(lote)`. Loga o
resultado só dos ciclos que processaram algo (com o worker ocioso, o log fica
quieto), e essa linha é a evidência do teste de falha do worker. Recebe tudo
pronto do composicao.py (o caso de uso e as exceções que significam "banco
fora") e não conhece o Postgres.

Tolerância a falhas:
- banco fora: uma linha de WARNING por ciclo, sem traceback, e nova tentativa
  no ciclo seguinte. O worker nunca morre por causa do banco. O primeiro ciclo
  bem-sucedido depois da queda loga "banco disponível novamente";
- erro inesperado: loga o traceback e segue. O lote sofreu rollback dentro do
  caso de uso e é reprocessado no próximo ciclo;
- SIGTERM (`docker stop`) ou SIGINT: termina o ciclo em andamento (a transação
  chega ao commit) e sai sem esperar o intervalo terminar.
"""

import logging
import signal
import threading
import time
from typing import Protocol

from app.dominio.entidades import ResultadoCiclo

log = logging.getLogger("worker")


class CasoDeUsoCiclo(Protocol):
    """O que o loop precisa do ProcessarPendentes."""

    def executar(self, limite: int) -> ResultadoCiclo: ...


class Evento(Protocol):
    """Subconjunto de threading.Event usado pelo loop (os testes usam um fake)."""

    def is_set(self) -> bool: ...
    def set(self) -> None: ...
    def wait(self, timeout: float | None = None) -> bool: ...


class Worker:
    def __init__(
        self,
        processar: CasoDeUsoCiclo,
        *,
        intervalo: float,
        lote: int,
        parar: Evento | None = None,
        erros_indisponibilidade: tuple[type[Exception], ...] = (),
    ) -> None:
        if intervalo <= 0:
            raise ValueError(f"WORKER_INTERVALO precisa ser maior que zero (recebido {intervalo})")
        if lote <= 0:
            raise ValueError(f"WORKER_LOTE precisa ser maior que zero (recebido {lote})")
        self._processar = processar
        self._intervalo = intervalo
        self._lote = lote
        self._indisponivel = erros_indisponibilidade
        self.parar: Evento = parar if parar is not None else threading.Event()
        self._banco_fora = False

    def ciclo(self) -> ResultadoCiclo | None:
        """Um ciclo. Devolve None se falhou (o lote continua pendente)."""
        inicio = time.monotonic()
        try:
            resultado = self._processar.executar(self._lote)
        except self._indisponivel as erro:
            self._banco_fora = True
            log.warning(
                "ciclo: banco indisponível (%s); nova tentativa em %gs",
                type(erro).__name__, self._intervalo,
            )
            return None
        except Exception:
            log.exception("ciclo: erro inesperado; o lote volta para a fila")
            return None

        if self._banco_fora:
            self._banco_fora = False
            log.info("ciclo: banco disponível novamente")
        if resultado.processadas == 0:
            return resultado
        log.info(
            "ciclo: processadas=%d alertas=%d backlog=%d duracao_ms=%d",
            resultado.processadas,
            resultado.alertas_criados,
            resultado.backlog,
            (time.monotonic() - inicio) * 1000,
        )
        return resultado

    def rodar(self) -> None:
        """Loop até `parar` ser acionado (por SIGTERM/SIGINT ou pelo teste)."""
        log.info("worker iniciado (intervalo=%gs, lote=%d)", self._intervalo, self._lote)
        while not self.parar.is_set():
            resultado = self.ciclo()
            # Lote cheio e ainda há pendentes: segue sem esperar, para o backlog
            # acumulado durante uma queda do worker esvaziar o mais rápido possível.
            if resultado and resultado.processadas >= self._lote and resultado.backlog > 0:
                continue
            # wait() em vez de sleep(): o SIGTERM acorda o loop na hora.
            self.parar.wait(self._intervalo)
        log.info("worker encerrado")


def instalar_sinais(parar: Evento) -> None:
    """SIGTERM e SIGINT acionam `parar`. Precisa ser chamado na thread principal.

    O handler só marca o evento: o ciclo em andamento termina normalmente, e o
    loop sai antes do próximo.
    """

    def ao_receber(numero: int, _frame: object) -> None:
        if not parar.is_set():
            log.info("%s recebido; encerrando depois do ciclo atual", signal.Signals(numero).name)
        parar.set()

    signal.signal(signal.SIGTERM, ao_receber)
    signal.signal(signal.SIGINT, ao_receber)
