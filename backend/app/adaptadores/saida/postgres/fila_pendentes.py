"""Fila de leituras pendentes sobre a própria tabela `leituras` (sem broker).

Contrato (dominio/portas.py, FilaPendentes.reservar):
- ao entrar: abre a transação e trava as pendentes com FOR UPDATE SKIP LOCKED;
- tudo o que o lote faz acontece nessa mesma transação;
- saída normal -> COMMIT; exceção -> ROLLBACK e o lote volta a ficar pendente.

Se o worker morrer no meio (kill, queda de rede), o Postgres desfaz a
transação ao perder a conexão e solta as travas: o efeito é o mesmo do rollback.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.adaptadores.saida.postgres import mapeamento
from app.adaptadores.saida.postgres.models import AlertaModel, LeituraModel
from app.dominio.entidades import Alerta, Leitura, StatusAlerta, TipoAlerta


class LotePendentePg:
    def __init__(self, sessao: Session, leituras: list[Leitura]) -> None:
        self._sessao = sessao
        self.leituras = leituras

    def tipos_abertos(self, paciente_id: str) -> set[TipoAlerta]:
        consulta = select(AlertaModel.tipo).where(
            AlertaModel.paciente_id == paciente_id,
            AlertaModel.status == StatusAlerta.ABERTO.value,
        )
        return {TipoAlerta(t) for t in self._sessao.scalars(consulta)}

    def registrar_alertas(self, alertas: list[Alerta]) -> None:
        if not alertas:
            return
        # O índice único parcial uq_alertas_aberto_por_tipo é a última linha de
        # defesa do anti-spam: se outro worker abriu o mesmo alerta neste meio
        # tempo, a linha é ignorada em vez de derrubar o lote inteiro.
        # Ordem fixa (paciente, tipo) para dois workers nunca esperarem um pelo
        # outro em ordem cruzada (deadlock) no índice único.
        ordenados = sorted(alertas, key=lambda a: (a.paciente_id, a.tipo.value))
        comando = (
            insert(AlertaModel)
            .values([mapeamento.alerta_para_linha(a) for a in ordenados])
            .on_conflict_do_nothing(
                index_elements=["paciente_id", "tipo"],
                index_where=AlertaModel.status == StatusAlerta.ABERTO.value,
            )
        )
        self._sessao.execute(comando)

    def marcar_processadas(self) -> None:
        if not self.leituras:
            return
        ids = [l.id for l in self.leituras]
        self._sessao.execute(
            update(LeituraModel).where(LeituraModel.id.in_(ids)).values(processada=True)
        )


class FilaPendentesPg:
    def __init__(self, sessoes: sessionmaker[Session]) -> None:
        self._sessoes = sessoes

    @contextmanager
    def reservar(self, limite: int) -> Iterator[LotePendentePg]:
        consulta = (
            select(LeituraModel)
            .where(LeituraModel.processada.is_(False))
            .order_by(LeituraModel.ts)
            .limit(limite)
            .with_for_update(skip_locked=True)
        )
        # Session.begin(): COMMIT na saída normal, ROLLBACK se houver exceção.
        with self._sessoes.begin() as s:
            leituras = [mapeamento.leitura(m) for m in s.scalars(consulta)]
            yield LotePendentePg(s, leituras)

    def contar_pendentes(self) -> int:
        with self._sessoes() as s:
            return s.scalar(
                select(func.count()).select_from(LeituraModel).where(LeituraModel.processada.is_(False))
            )
