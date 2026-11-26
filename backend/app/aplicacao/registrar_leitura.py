"""Caso de uso: registrar uma leitura enviada por um sensor (POST /leituras)."""

from collections.abc import Callable
from datetime import datetime

from app.dominio.entidades import Leitura
from app.dominio.erros import PacienteNaoEncontrado
from app.dominio.portas import RepositorioLeituras, RepositorioPacientes


class RegistrarLeitura:
    def __init__(
        self,
        pacientes: RepositorioPacientes,
        leituras: RepositorioLeituras,
        relogio: Callable[[], datetime],
    ) -> None:
        self._pacientes = pacientes
        self._leituras = leituras
        self._relogio = relogio

    def executar(self, leitura: Leitura) -> bool:
        """Grava a leitura. True = nova (201), False = id já existia (200).

        Levanta LeituraInvalida (ts mais de 5 min no futuro) ou
        PacienteNaoEncontrado. A validação de faixa já aconteceu ao criar a Leitura.
        """
        leitura.validar_horario(self._relogio())
        if not self._pacientes.existe(leitura.paciente_id):
            raise PacienteNaoEncontrado(leitura.paciente_id)
        return self._leituras.salvar_se_nova(leitura)
