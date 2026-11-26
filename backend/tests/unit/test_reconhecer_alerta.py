from uuid import uuid4

import pytest

from app.aplicacao.reconhecer_alerta import ReconhecerAlerta
from app.dominio.entidades import Alerta, Severidade, StatusAlerta, TipoAlerta
from app.dominio.erros import AlertaNaoEncontrado
from tests.fakes import RepositorioAlertasFake


class AlertasContandoAtualizacoes(RepositorioAlertasFake):
    def __init__(self, banco) -> None:
        super().__init__(banco)
        self.atualizacoes = 0

    def atualizar_status(self, alerta_id, status) -> None:
        self.atualizacoes += 1
        super().atualizar_status(alerta_id, status)


@pytest.fixture
def alertas(banco):
    return AlertasContandoAtualizacoes(banco)


@pytest.fixture
def alerta_id(banco) -> int:
    alerta = Alerta("P001", uuid4(), TipoAlerta.SPO2_BAIXA, 87.5, Severidade.ALTA)
    return banco.inserir_alerta(alerta).id


def test_reconhece_alerta_aberto(banco, alertas, alerta_id):
    resultado = ReconhecerAlerta(alertas).executar(alerta_id)

    assert resultado.status == StatusAlerta.RECONHECIDO
    assert resultado.id == alerta_id
    assert banco.alertas[alerta_id].status == StatusAlerta.RECONHECIDO


def test_reconhecer_de_novo_nao_da_erro_nem_grava_outra_vez(alertas, alerta_id):
    caso_de_uso = ReconhecerAlerta(alertas)
    caso_de_uso.executar(alerta_id)
    resultado = caso_de_uso.executar(alerta_id)

    assert resultado.status == StatusAlerta.RECONHECIDO
    assert alertas.atualizacoes == 1


def test_alerta_inexistente(alertas):
    with pytest.raises(AlertaNaoEncontrado):
        ReconhecerAlerta(alertas).executar(999)
