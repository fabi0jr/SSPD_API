"""Helpers dos testes de integração."""

from sqlalchemy.dialects.postgresql import insert

from app.adaptadores.saida.postgres import mapeamento
from app.adaptadores.saida.postgres.models import AlertaModel
from app.dominio.entidades import Alerta


def inserir_alerta(sessoes, alerta: Alerta) -> int:
    """Grava um alerta direto na tabela (o repositório não tem 'criar') e devolve o id."""
    with sessoes.begin() as s:
        return s.scalar(
            insert(AlertaModel).values(**mapeamento.alerta_para_linha(alerta)).returning(AlertaModel.id)
        )
