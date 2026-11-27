"""Composição (injeção de dependência manual).

Único lugar que conhece as implementações concretas: liga os adaptadores de
saída (Postgres) aos casos de uso e entrega tudo aos adaptadores de entrada.

Entrypoints:
- API: uvicorn app.composicao:criar_app_http --factory
- worker: python -m app.composicao worker
"""

import logging
import sys
from datetime import datetime, timezone

from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app import config
from app.adaptadores.entrada.http.deps import Componentes
from app.adaptadores.entrada.http.main import criar_app
from app.adaptadores.entrada.http.seguranca import ConfigJwt, gerar_hash_senha, verificar_senha
from app.adaptadores.entrada.worker.main import Evento, Worker, instalar_sinais
from app.adaptadores.saida.postgres.db import (
    aguardar_banco,
    criar_engine,
    criar_fabrica_sessoes,
    verificar_banco,
)
from app.adaptadores.saida.postgres.fila_pendentes import FilaPendentesPg
from app.adaptadores.saida.postgres.repositorios import (
    RepositorioAlertasPg,
    RepositorioLeiturasPg,
    RepositorioPacientesPg,
    RepositorioUsuariosPg,
)
from app.aplicacao.autenticar import Autenticar
from app.aplicacao.cadastrar_paciente import CadastrarPaciente
from app.aplicacao.cadastrar_usuario import CadastrarUsuario
from app.aplicacao.consultas import Consultas
from app.aplicacao.processar_pendentes import ProcessarPendentes
from app.aplicacao.reconhecer_alerta import ReconhecerAlerta
from app.aplicacao.registrar_leitura import RegistrarLeitura

log = logging.getLogger("composicao")

# Conexão recusada/caída e pool esgotado: o banco está fora ou sobrecarregado.
ERROS_INDISPONIBILIDADE = (OperationalError, PoolTimeoutError)


def relogio_utc() -> datetime:
    return datetime.now(timezone.utc)


def configurar_logs() -> None:
    """Uma linha por evento, em stdout, com timestamp (CLAUDE.md, seção 14)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
    )


def montar_componentes_http(engine: Engine) -> Componentes:
    sessoes = criar_fabrica_sessoes(engine)
    pacientes = RepositorioPacientesPg(sessoes)
    leituras = RepositorioLeiturasPg(sessoes)
    alertas = RepositorioAlertasPg(sessoes)
    usuarios = RepositorioUsuariosPg(sessoes)
    return Componentes(
        registrar_leitura=RegistrarLeitura(pacientes, leituras, relogio=relogio_utc),
        autenticar=Autenticar(usuarios, verificar_senha),
        cadastrar_usuario=CadastrarUsuario(usuarios, gerar_hash_senha),
        cadastrar_paciente=CadastrarPaciente(pacientes),
        consultas=Consultas(pacientes, leituras, alertas),
        reconhecer_alerta=ReconhecerAlerta(alertas),
        verificar_banco=lambda: verificar_banco(engine),
    )


def criar_app_http() -> FastAPI:
    """Fábrica para o uvicorn (--factory). Lê o ambiente e monta a API."""
    configurar_logs()
    # Variáveis obrigatórias primeiro: sem elas, falhar já no start é o certo.
    sensor_api_key = config.obrigatoria("SENSOR_API_KEY")
    try:
        jwt = ConfigJwt(
            segredo=config.obrigatoria("JWT_SECRET"),
            expiracao_minutos=config.inteiro("JWT_EXP_MINUTES", 60),
        )
    except ValueError as erro:  # segredo curto ou expiração inválida
        raise config.ConfiguracaoAusente(str(erro)) from None
    engine = criar_engine(config.database_url())

    try:
        aguardar_banco(engine)
    except Exception:
        # A API sobe mesmo assim: /health responde 503 e o pool reconecta
        # sozinho quando o banco voltar. Melhor do que reiniciar em loop.
        log.error("banco ainda indisponível; API subindo em modo degradado")

    return criar_app(
        montar_componentes_http(engine),
        sensor_api_key=sensor_api_key,
        jwt=jwt,
        cors_origins=config.lista("CORS_ORIGINS"),
        ao_encerrar=engine.dispose,
        erros_indisponibilidade=ERROS_INDISPONIBILIDADE,
    )


def montar_worker(engine: Engine, *, intervalo: float, lote: int, parar: Evento | None = None) -> Worker:
    fila = FilaPendentesPg(criar_fabrica_sessoes(engine))
    return Worker(
        ProcessarPendentes(fila),
        intervalo=intervalo,
        lote=lote,
        parar=parar,
        erros_indisponibilidade=ERROS_INDISPONIBILIDADE,
    )


def rodar_worker() -> None:
    """Entrypoint do worker. Lê o ambiente, monta o loop e roda até o SIGTERM.

    Não chama aguardar_banco: o próprio loop já tenta de novo a cada ciclo, sem
    prazo, e um sleep longo no startup atrasaria a resposta ao `docker stop`.
    """
    configurar_logs()
    url = config.database_url()
    intervalo = config.inteiro("WORKER_INTERVALO", 2)
    lote = config.inteiro("WORKER_LOTE", 500)
    # Uma sessão por vez (reservar, depois contar_pendentes): pool mínimo.
    engine = criar_engine(url, pool_size=1, max_overflow=1)
    try:
        try:
            worker = montar_worker(engine, intervalo=intervalo, lote=lote)
        except ValueError as erro:  # intervalo ou lote <= 0
            raise config.ConfiguracaoAusente(str(erro)) from None
        instalar_sinais(worker.parar)
        worker.rodar()
    finally:
        engine.dispose()


if __name__ == "__main__":
    if sys.argv[1:] != ["worker"]:
        sys.exit("uso: python -m app.composicao worker")
    rodar_worker()
