"""Caso de uso: um profissional reconhece um alerta (PATCH /alertas/{id})."""

from dataclasses import replace

from app.dominio.entidades import Alerta, StatusAlerta
from app.dominio.erros import AlertaNaoEncontrado
from app.dominio.portas import RepositorioAlertas


class ReconhecerAlerta:
    def __init__(self, alertas: RepositorioAlertas) -> None:
        self._alertas = alertas

    def executar(self, alerta_id: int) -> Alerta:
        """Marca o alerta como reconhecido e o devolve.

        Idempotente: reconhecer de novo não dá erro nem grava outra vez.
        Levanta AlertaNaoEncontrado se o id não existir.

        Depois de reconhecido, o alerta deixa de bloquear o anti-spam: uma nova
        leitura alterada do mesmo tipo volta a gerar alerta.
        """
        alerta = self._alertas.obter(alerta_id)
        if alerta is None:
            raise AlertaNaoEncontrado(alerta_id)
        if alerta.status != StatusAlerta.RECONHECIDO:
            self._alertas.atualizar_status(alerta_id, StatusAlerta.RECONHECIDO)
            alerta = replace(alerta, status=StatusAlerta.RECONHECIDO)
        return alerta
