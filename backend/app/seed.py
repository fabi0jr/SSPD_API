"""Seed idempotente: usuário admin e pacientes fictícios.

Uso:  python -m app.seed   (no serviço migrate, depois de `alembic upgrade head`)

Pode rodar a cada deploy: o que já existe não é alterado. Os pacientes são
P001, P002, ... até SEED_PACIENTES, os mesmos códigos usados pelo simulador.
Os dados são fictícios (LGPD: dados de saúde são dados pessoais sensíveis).

Entrypoint de infraestrutura, como o composicao.py: por isso fala direto com
o adaptador Postgres.
"""

import logging
import sys

import bcrypt
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert

from app import config
from app.adaptadores.saida.postgres.db import aguardar_banco, criar_engine
from app.adaptadores.saida.postgres.models import PacienteModel, UsuarioModel

log = logging.getLogger("seed")


def codigo_paciente(n: int) -> str:
    return f"P{n:03d}"


def gerar_hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def semear(engine: Engine, admin_email: str, admin_senha: str, n_pacientes: int) -> tuple[int, int]:
    """Devolve (usuarios_criados, pacientes_criados)."""
    pacientes = [
        {"id": codigo_paciente(i), "nome": f"Paciente Fictício {i:03d}", "leito": f"L-{i:03d}"}
        for i in range(1, n_pacientes + 1)
    ]
    with engine.begin() as conexao:
        # RETURNING devolve só as linhas inseridas (rowcount não é confiável aqui).
        usuarios_criados = len(conexao.execute(
            insert(UsuarioModel)
            .values(email=admin_email, senha_hash=gerar_hash_senha(admin_senha))
            .on_conflict_do_nothing(index_elements=["email"])
            .returning(UsuarioModel.id)
        ).all())
        pacientes_criados = 0
        if pacientes:
            pacientes_criados = len(conexao.execute(
                insert(PacienteModel)
                .values(pacientes)
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(PacienteModel.id)
            ).all())
    return usuarios_criados, pacientes_criados


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
    )
    engine = criar_engine(config.database_url(), pool_size=1, max_overflow=0)
    try:
        aguardar_banco(engine)
        usuarios, pacientes = semear(
            engine,
            admin_email=config.obrigatoria("ADMIN_EMAIL"),
            admin_senha=config.obrigatoria("ADMIN_PASSWORD"),
            n_pacientes=config.inteiro("SEED_PACIENTES", 50),
        )
    finally:
        engine.dispose()
    log.info("seed concluído: %d usuário(s) e %d paciente(s) criados", usuarios, pacientes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
