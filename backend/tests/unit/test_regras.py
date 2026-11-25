from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.dominio.entidades import Leitura, Severidade, StatusAlerta, TipoAlerta
from app.dominio.regras import avaliar

TS = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


def leitura(fc: int = 80, spo2: float = 97.0, temp: float = 36.5) -> Leitura:
    return Leitura(id=uuid4(), paciente_id="P001", fc=fc, spo2=spo2, temp=temp, ts=TS)


def tipos(l: Leitura) -> list[TipoAlerta]:
    return [a.tipo for a in avaliar(l)]


def test_leitura_normal_nao_gera_alerta():
    assert avaliar(leitura()) == []


@pytest.mark.parametrize(
    ("spo2", "esperado"),
    [(90.0, []), (89.9, [TipoAlerta.SPO2_BAIXA]), (50.0, [TipoAlerta.SPO2_BAIXA]), (100.0, [])],
)
def test_spo2_borda(spo2, esperado):
    assert tipos(leitura(spo2=spo2)) == esperado


@pytest.mark.parametrize(
    ("fc", "esperado"),
    [
        (120, []),
        (121, [TipoAlerta.TAQUICARDIA]),
        (50, []),
        (49, [TipoAlerta.BRADICARDIA]),
    ],
)
def test_fc_borda(fc, esperado):
    assert tipos(leitura(fc=fc)) == esperado


@pytest.mark.parametrize(
    ("temp", "esperado"),
    [
        (37.9, []),
        (38.0, [TipoAlerta.FEBRE]),
        (35.0, []),
        (34.9, [TipoAlerta.HIPOTERMIA]),
    ],
)
def test_temp_borda(temp, esperado):
    assert tipos(leitura(temp=temp)) == esperado


def test_varios_alertas_na_mesma_leitura_em_ordem_fixa():
    assert tipos(leitura(fc=130, spo2=85.0, temp=39.0)) == [
        TipoAlerta.SPO2_BAIXA,
        TipoAlerta.TAQUICARDIA,
        TipoAlerta.FEBRE,
    ]


@pytest.mark.parametrize(
    ("kwargs", "tipo", "severidade", "valor"),
    [
        ({"spo2": 88.5}, TipoAlerta.SPO2_BAIXA, Severidade.ALTA, 88.5),
        ({"fc": 140}, TipoAlerta.TAQUICARDIA, Severidade.MEDIA, 140),
        ({"fc": 40}, TipoAlerta.BRADICARDIA, Severidade.MEDIA, 40),
        ({"temp": 38.7}, TipoAlerta.FEBRE, Severidade.MEDIA, 38.7),
        ({"temp": 34.0}, TipoAlerta.HIPOTERMIA, Severidade.ALTA, 34.0),
    ],
)
def test_campos_do_alerta(kwargs, tipo, severidade, valor):
    l = leitura(**kwargs)
    [alerta] = avaliar(l)
    assert alerta.tipo == tipo
    assert alerta.severidade == severidade
    assert alerta.valor == valor
    assert alerta.paciente_id == l.paciente_id
    assert alerta.leitura_id == l.id
    assert alerta.status == StatusAlerta.ABERTO
    assert alerta.id is None
