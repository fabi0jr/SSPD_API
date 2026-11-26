"""Consultas de leitura usadas pelo dashboard (rotas GET).

Não alteram nada. Ficam juntas numa classe só porque são finas: a lógica que
importa aqui é responder 404 quando o paciente não existe, em vez de lista vazia.
"""

from datetime import datetime

from app.dominio.entidades import Alerta, Leitura, ResumoPaciente, StatusAlerta
from app.dominio.erros import PacienteNaoEncontrado
from app.dominio.portas import RepositorioAlertas, RepositorioLeituras, RepositorioPacientes

LIMITE_PADRAO = 100
LIMITE_MAXIMO = 1000


class Consultas:
    def __init__(
        self,
        pacientes: RepositorioPacientes,
        leituras: RepositorioLeituras,
        alertas: RepositorioAlertas,
    ) -> None:
        self._pacientes = pacientes
        self._leituras = leituras
        self._alertas = alertas

    def listar_pacientes(self) -> list[ResumoPaciente]:
        return self._pacientes.listar_com_resumo()

    def ultima_leitura(self, paciente_id: str) -> Leitura | None:
        """None quando o paciente existe mas ainda não mandou nenhuma leitura."""
        self._garantir_paciente(paciente_id)
        return self._leituras.ultima(paciente_id)

    def historico(
        self,
        paciente_id: str,
        desde: datetime | None = None,
        ate: datetime | None = None,
        limite: int = LIMITE_PADRAO,
    ) -> list[Leitura]:
        """Leituras por ts decrescente. O limite é cortado em LIMITE_MAXIMO."""
        self._garantir_paciente(paciente_id)
        return self._leituras.historico(paciente_id, desde, ate, _limitar(limite))

    def listar_alertas(
        self,
        status: StatusAlerta | None = None,
        paciente_id: str | None = None,
        limite: int = LIMITE_PADRAO,
    ) -> list[Alerta]:
        """Alertas por criado_em decrescente. Filtrar por paciente inexistente dá lista vazia."""
        return self._alertas.listar(status, paciente_id, _limitar(limite))

    def _garantir_paciente(self, paciente_id: str) -> None:
        if not self._pacientes.existe(paciente_id):
            raise PacienteNaoEncontrado(paciente_id)


def _limitar(limite: int) -> int:
    return max(1, min(limite, LIMITE_MAXIMO))
