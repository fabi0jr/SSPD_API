"""Repositórios Postgres. Cada método abre e fecha a própria transação curta.

A API é stateless: não há sessão longa nem estado entre requisições.
"""

from datetime import datetime

from sqlalchemy import func, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.adaptadores.saida.postgres import mapeamento
from app.adaptadores.saida.postgres.models import (
    AlertaModel,
    LeituraModel,
    PacienteModel,
    UsuarioModel,
)
from app.dominio.entidades import Alerta, Leitura, Paciente, ResumoPaciente, StatusAlerta, Usuario
from app.dominio.erros import PacienteJaExiste, UsuarioJaExiste


class RepositorioPacientesPg:
    def __init__(self, sessoes: sessionmaker[Session]) -> None:
        self._sessoes = sessoes

    def existe(self, paciente_id: str) -> bool:
        with self._sessoes() as s:
            return s.scalar(select(PacienteModel.id).where(PacienteModel.id == paciente_id)) is not None

    def obter(self, paciente_id: str) -> Paciente | None:
        with self._sessoes() as s:
            m = s.get(PacienteModel, paciente_id)
            return mapeamento.paciente(m) if m else None

    def listar_com_resumo(self) -> list[ResumoPaciente]:
        # Uma consulta só: LATERAL pega a última leitura de cada paciente
        # (índice ix_leituras_paciente_ts) e a subconsulta conta os abertos
        # (índice parcial uq_alertas_aberto_por_tipo).
        ultima_sq = (
            select(LeituraModel)
            .where(LeituraModel.paciente_id == PacienteModel.id)
            .order_by(LeituraModel.ts.desc())
            .limit(1)
            .lateral("ultima")
        )
        ultima = aliased(LeituraModel, ultima_sq)
        abertos = (
            select(func.count())
            .where(
                AlertaModel.paciente_id == PacienteModel.id,
                AlertaModel.status == StatusAlerta.ABERTO.value,
            )
            .scalar_subquery()
        )
        consulta = (
            select(PacienteModel, ultima, abertos)
            .outerjoin(ultima_sq, true())
            .order_by(PacienteModel.id)
        )
        with self._sessoes() as s:
            return [
                ResumoPaciente(
                    paciente=mapeamento.paciente(p),
                    ultima_leitura=mapeamento.leitura(l) if l is not None else None,
                    alertas_abertos=n,
                )
                for p, l, n in s.execute(consulta)
            ]

    def criar(self, paciente: Paciente) -> None:
        comando = (
            insert(PacienteModel)
            .values(id=paciente.id, nome=paciente.nome, leito=paciente.leito)
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(PacienteModel.id)
        )
        with self._sessoes.begin() as s:
            if s.scalar(comando) is None:
                raise PacienteJaExiste(paciente.id)


class RepositorioLeiturasPg:
    def __init__(self, sessoes: sessionmaker[Session]) -> None:
        self._sessoes = sessoes

    def salvar_se_nova(self, leitura: Leitura) -> bool:
        # ON CONFLICT só no id: violação de FK (paciente inexistente) ainda é erro.
        comando = (
            insert(LeituraModel)
            .values(**mapeamento.leitura_para_linha(leitura))
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(LeituraModel.id)
        )
        with self._sessoes.begin() as s:
            return s.scalar(comando) is not None

    def ultima(self, paciente_id: str) -> Leitura | None:
        historico = self.historico(paciente_id, None, None, 1)
        return historico[0] if historico else None

    def historico(
        self,
        paciente_id: str,
        desde: datetime | None,
        ate: datetime | None,
        limite: int,
    ) -> list[Leitura]:
        consulta = select(LeituraModel).where(LeituraModel.paciente_id == paciente_id)
        if desde is not None:
            consulta = consulta.where(LeituraModel.ts >= desde)
        if ate is not None:
            consulta = consulta.where(LeituraModel.ts <= ate)
        consulta = consulta.order_by(LeituraModel.ts.desc()).limit(limite)
        with self._sessoes() as s:
            return [mapeamento.leitura(m) for m in s.scalars(consulta)]


class RepositorioAlertasPg:
    def __init__(self, sessoes: sessionmaker[Session]) -> None:
        self._sessoes = sessoes

    def listar(
        self,
        status: StatusAlerta | None,
        paciente_id: str | None,
        limite: int,
    ) -> list[Alerta]:
        consulta = select(AlertaModel)
        if status is not None:
            consulta = consulta.where(AlertaModel.status == status.value)
        if paciente_id is not None:
            consulta = consulta.where(AlertaModel.paciente_id == paciente_id)
        consulta = consulta.order_by(AlertaModel.criado_em.desc(), AlertaModel.id.desc()).limit(limite)
        with self._sessoes() as s:
            return [mapeamento.alerta(m) for m in s.scalars(consulta)]

    def obter(self, alerta_id: int) -> Alerta | None:
        with self._sessoes() as s:
            m = s.get(AlertaModel, alerta_id)
            return mapeamento.alerta(m) if m else None

    def atualizar_status(self, alerta_id: int, status: StatusAlerta) -> None:
        with self._sessoes.begin() as s:
            s.execute(update(AlertaModel).where(AlertaModel.id == alerta_id).values(status=status.value))


class RepositorioUsuariosPg:
    def __init__(self, sessoes: sessionmaker[Session]) -> None:
        self._sessoes = sessoes

    def por_email(self, email: str) -> Usuario | None:
        with self._sessoes() as s:
            m = s.scalar(select(UsuarioModel).where(UsuarioModel.email == email))
            return mapeamento.usuario(m) if m else None

    def criar(self, email: str, senha_hash: str) -> Usuario:
        # Um comando só, apoiado no UNIQUE de usuarios.email: sem "consultar e
        # depois inserir", que teria corrida entre as duas réplicas da API.
        comando = (
            insert(UsuarioModel)
            .values(email=email, senha_hash=senha_hash)
            .on_conflict_do_nothing(index_elements=["email"])
            .returning(UsuarioModel.id)
        )
        with self._sessoes.begin() as s:
            usuario_id = s.scalar(comando)
        if usuario_id is None:
            raise UsuarioJaExiste(email)
        return Usuario(id=usuario_id, email=email, senha_hash=senha_hash)
