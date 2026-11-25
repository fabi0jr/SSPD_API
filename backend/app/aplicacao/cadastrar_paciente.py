"""Caso de uso: cadastrar um paciente (POST /pacientes)."""

from app.dominio.entidades import Paciente
from app.dominio.portas import RepositorioPacientes


class CadastrarPaciente:
    def __init__(self, pacientes: RepositorioPacientes) -> None:
        self._pacientes = pacientes

    def executar(self, paciente: Paciente) -> Paciente:
        """Levanta PacienteJaExiste se o id já estiver em uso.

        A validação dos campos já aconteceu ao criar o Paciente (PacienteInvalido).
        Devolve o paciente como ficou gravado (com criado_em).
        """
        self._pacientes.criar(paciente)
        return self._pacientes.obter(paciente.id) or paciente
