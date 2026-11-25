"""Engine, fábrica de sessões, retry de conexão no startup e health check."""

import logging
import time

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)


def criar_engine(url: str, pool_size: int = 5, max_overflow: int = 5) -> Engine:
    """Engine com pool pequeno e resistente a quedas do Postgres.

    - pool_pre_ping: testa a conexão antes de usar. Depois de um restart do
      Postgres, as conexões mortas do pool são descartadas em vez de virarem
      erro 500.
    - connect_timeout: com o banco fora, /health responde 503 em poucos
      segundos em vez de travar a requisição.
    - timezone=UTC: os TIMESTAMPTZ voltam sempre em UTC.

    Conexões por processo: até pool_size + max_overflow (10). Com 2 réplicas
    da API + worker, ficam bem abaixo do max_connections padrão (100).
    """
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_timeout=10,
        connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"},
    )


def criar_fabrica_sessoes(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def verificar_banco(engine: Engine) -> bool:
    """SELECT 1. Usado pelo /health. Nunca levanta exceção."""
    try:
        with engine.connect() as conexao:
            conexao.execute(text("SELECT 1"))
        return True
    except Exception as erro:
        # Uma linha só: o healthcheck roda a cada poucos segundos e um
        # traceback por chamada afogaria o log durante uma queda do banco.
        log.warning("health check: banco indisponível (%s)", type(erro).__name__)
        return False


def aguardar_banco(engine: Engine, timeout: float = 30.0, espera_inicial: float = 0.5) -> None:
    """Bloqueia até o banco responder, com backoff exponencial (até `timeout` s).

    Chamado no startup da API, do worker e do seed, para nenhum deles morrer
    se subir antes do Postgres. Depois do timeout, levanta a última exceção
    (o container sai com erro e o restart policy do Compose tenta de novo).
    """
    inicio = time.monotonic()
    espera = espera_inicial
    tentativa = 1
    while True:
        try:
            with engine.connect() as conexao:
                conexao.execute(text("SELECT 1"))
            if tentativa > 1:
                log.info("banco disponível após %d tentativas", tentativa)
            return
        except Exception as erro:
            decorrido = time.monotonic() - inicio
            if decorrido + espera > timeout:
                log.error("banco indisponível após %.1fs; desistindo", decorrido)
                raise
            log.warning(
                "banco indisponível (tentativa %d, %s); nova tentativa em %.1fs",
                tentativa, type(erro).__name__, espera,
            )
            time.sleep(espera)
            espera = min(espera * 2, 5.0)
            tentativa += 1
