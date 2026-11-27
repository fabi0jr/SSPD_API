"""Portas (contratos) que os adaptadores de saída implementam.

Os casos de uso dependem só destes Protocols. As implementações concretas
(Postgres, fakes em memória) são escolhidas em composicao.py ou nos testes.
"""

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from app.dominio.entidades import (
    Alerta,
    Leitura,
    Paciente,
    ResumoPaciente,
    StatusAlerta,
    TipoAlerta,
    Usuario,
)


class RepositorioPacientes(Protocol):
    def existe(self, paciente_id: str) -> bool: ...
    def obter(self, paciente_id: str) -> Paciente | None: ...
    def listar_com_resumo(self) -> list[ResumoPaciente]: ...

    def criar(self, paciente: Paciente) -> None:
        """Levanta PacienteJaExiste se o id já estiver em uso."""
        ...


class RepositorioLeituras(Protocol):
    def salvar_se_nova(self, leitura: Leitura) -> bool:
        """Grava a leitura. Devolve False, sem erro, se o id já existia (idempotência)."""
        ...

    def ultima(self, paciente_id: str) -> Leitura | None: ...

    def historico(
        self,
        paciente_id: str,
        desde: datetime | None,
        ate: datetime | None,
        limite: int,
    ) -> list[Leitura]:
        """Leituras ordenadas por ts decrescente."""
        ...


class RepositorioAlertas(Protocol):
    def listar(
        self,
        status: StatusAlerta | None,
        paciente_id: str | None,
        limite: int,
    ) -> list[Alerta]:
        """Alertas ordenados por criado_em decrescente."""
        ...

    def obter(self, alerta_id: int) -> Alerta | None: ...
    def atualizar_status(self, alerta_id: int, status: StatusAlerta) -> None: ...


class RepositorioUsuarios(Protocol):
    def por_email(self, email: str) -> Usuario | None: ...

    def criar(self, email: str, senha_hash: str) -> Usuario:
        """Grava o usuário e o devolve com o id. Levanta UsuarioJaExiste se o e-mail já estiver em uso.

        A unicidade é garantida pelo armazenamento (índice único), não por uma
        consulta antes do insert: duas réplicas da API podem cadastrar ao mesmo tempo.
        """
        ...


class LotePendente(Protocol):
    """Lote de leituras reservado pelo worker. Tudo aqui roda numa mesma transação."""

    leituras: list[Leitura]

    def tipos_abertos(self, paciente_id: str) -> set[TipoAlerta]:
        """Tipos com alerta 'aberto' no banco para o paciente (anti-spam)."""
        ...

    def registrar_alertas(self, alertas: list[Alerta]) -> None: ...

    def marcar_processadas(self) -> None:
        """Marca todas as leituras do lote como processadas."""
        ...


class FilaPendentes(Protocol):
    def reservar(self, limite: int) -> AbstractContextManager[LotePendente]:
        """Reserva até `limite` leituras pendentes, as mais antigas primeiro.

        Contrato:
        - ao entrar: abre a transação e trava as linhas (FOR UPDATE SKIP LOCKED);
        - saída normal: COMMIT;
        - saída por exceção: ROLLBACK, e o lote volta a ficar pendente.
        """
        ...

    def contar_pendentes(self) -> int:
        """Tamanho do backlog (leituras com processada = false)."""
        ...
