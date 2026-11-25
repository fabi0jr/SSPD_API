"""Tabelas iniciais: pacientes, leituras, alertas e usuarios.

Revision ID: 0001
Revises:
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pacientes",
        sa.Column("id", sa.String(10), primary_key=True),
        sa.Column("nome", sa.String(100), nullable=False),
        sa.Column("leito", sa.String(20), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "leituras",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("paciente_id", sa.String(10), sa.ForeignKey("pacientes.id"), nullable=False),
        sa.Column("fc", sa.SmallInteger(), nullable=False),
        sa.Column("spo2", sa.Numeric(4, 1), nullable=False),
        sa.Column("temp", sa.Numeric(3, 1), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recebido_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processada", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index("ix_leituras_paciente_ts", "leituras", ["paciente_id", sa.text("ts DESC")])
    op.create_index(
        "ix_leituras_pendentes", "leituras", ["ts"], postgresql_where=sa.text("processada = false")
    )

    op.create_table(
        "alertas",
        sa.Column("id", sa.BigInteger(), primary_key=True),  # BIGSERIAL
        sa.Column("paciente_id", sa.String(10), sa.ForeignKey("pacientes.id"), nullable=False),
        sa.Column("leitura_id", sa.Uuid(), sa.ForeignKey("leituras.id"), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("valor", sa.Numeric(5, 1), nullable=False),
        sa.Column("severidade", sa.String(10), nullable=False),
        sa.Column("status", sa.String(15), server_default=sa.text("'aberto'"), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_alertas_status_criado_em", "alertas", ["status", sa.text("criado_em DESC")])
    op.create_index(
        "uq_alertas_aberto_por_tipo",
        "alertas",
        ["paciente_id", "tipo"],
        unique=True,
        postgresql_where=sa.text("status = 'aberto'"),
    )

    op.create_table(
        "usuarios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(120), nullable=False, unique=True),
        sa.Column("senha_hash", sa.String(200), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("usuarios")
    op.drop_table("alertas")
    op.drop_table("leituras")
    op.drop_table("pacientes")
