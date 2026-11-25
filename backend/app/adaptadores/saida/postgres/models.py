"""Tabelas SQLAlchemy (CLAUDE.md, seção 7). Só este módulo conhece o ORM.

Qualquer mudança aqui precisa de uma migration nova em migrations/versions
(o teste test_migrations_batem_com_os_models falha se esquecer).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PacienteModel(Base):
    __tablename__ = "pacientes"

    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    nome: Mapped[str] = mapped_column(String(100))
    leito: Mapped[str | None] = mapped_column(String(20))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LeituraModel(Base):
    __tablename__ = "leituras"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)  # gerado pelo sensor
    paciente_id: Mapped[str] = mapped_column(String(10), ForeignKey("pacientes.id"))
    fc: Mapped[int] = mapped_column(SmallInteger)
    spo2: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    temp: Mapped[Decimal] = mapped_column(Numeric(3, 1))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recebido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processada: Mapped[bool] = mapped_column(Boolean, server_default=false())

    __table_args__ = (
        Index("ix_leituras_paciente_ts", "paciente_id", text("ts DESC")),
        # Fila do worker: só as pendentes, já na ordem em que são consumidas.
        Index("ix_leituras_pendentes", "ts", postgresql_where=text("processada = false")),
    )


class AlertaModel(Base):
    __tablename__ = "alertas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # BIGSERIAL
    paciente_id: Mapped[str] = mapped_column(String(10), ForeignKey("pacientes.id"))
    leitura_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("leituras.id"))
    tipo: Mapped[str] = mapped_column(String(30))
    valor: Mapped[Decimal] = mapped_column(Numeric(5, 1))
    severidade: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(15), server_default=text("'aberto'"))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_alertas_status_criado_em", "status", text("criado_em DESC")),
        # Anti-spam garantido pelo banco: no máximo um alerta aberto por
        # (paciente, tipo), mesmo com mais de um worker rodando ao mesmo tempo.
        # Também serve de índice para tipos_abertos e para a contagem de abertos.
        Index(
            "uq_alertas_aberto_por_tipo",
            "paciente_id",
            "tipo",
            unique=True,
            postgresql_where=text("status = 'aberto'"),
        ),
    )


class UsuarioModel(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # SERIAL
    email: Mapped[str] = mapped_column(String(120), unique=True)
    senha_hash: Mapped[str] = mapped_column(String(200))
