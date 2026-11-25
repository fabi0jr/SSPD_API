from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.dominio.entidades import Leitura, Paciente
from app.dominio.erros import LeituraInvalida, PacienteInvalido

AGORA = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


def leitura(**kwargs) -> Leitura:
    dados = {"id": uuid4(), "paciente_id": "P001", "fc": 80, "spo2": 97.0, "temp": 36.5, "ts": AGORA}
    dados.update(kwargs)
    return Leitura(**dados)


# --- Faixas (limites inclusivos) -------------------------------------------------

@pytest.mark.parametrize(
    ("campo", "valor"),
    [("fc", 20), ("fc", 250), ("spo2", 50.0), ("spo2", 100.0), ("temp", 30.0), ("temp", 43.0)],
)
def test_limites_da_faixa_sao_aceitos(campo, valor):
    leitura(**{campo: valor})


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("fc", 19), ("fc", 251), ("spo2", 49.9), ("spo2", 100.1), ("temp", 29.9), ("temp", 43.1)],
)
def test_fora_da_faixa_e_rejeitado(campo, valor):
    with pytest.raises(LeituraInvalida) as erro:
        leitura(**{campo: valor})
    assert erro.value.campo == campo


def test_ts_sem_fuso_e_rejeitado():
    with pytest.raises(LeituraInvalida) as erro:
        leitura(ts=datetime(2026, 11, 25, 14, 0))
    assert erro.value.campo == "ts"


def test_ts_com_outro_fuso_e_aceito():
    manaus = timezone(timedelta(hours=-4))
    leitura(ts=datetime(2026, 11, 25, 10, 0, tzinfo=manaus))


# --- Horário em relação ao "agora" -----------------------------------------------

def test_ts_no_passado_e_valido():
    leitura(ts=AGORA - timedelta(days=1)).validar_horario(AGORA)


def test_ts_exatamente_5_min_no_futuro_e_valido():
    leitura(ts=AGORA + timedelta(minutes=5)).validar_horario(AGORA)


def test_ts_mais_de_5_min_no_futuro_e_rejeitado():
    with pytest.raises(LeituraInvalida) as erro:
        leitura(ts=AGORA + timedelta(minutes=5, seconds=1)).validar_horario(AGORA)
    assert erro.value.campo == "ts"


# --- Paciente --------------------------------------------------------------------

def test_paciente_valido():
    p = Paciente(id="P001", nome="Paciente Fictício 1", leito="UTI-01")
    assert p.id == "P001"


@pytest.mark.parametrize(
    ("kwargs", "campo"),
    [
        ({"id": "", "nome": "X"}, "id"),
        ({"id": "   ", "nome": "X"}, "id"),
        ({"id": "P" * 11, "nome": "X"}, "id"),
        ({"id": "P001", "nome": ""}, "nome"),
        ({"id": "P001", "nome": "x" * 101}, "nome"),
    ],
)
def test_paciente_invalido(kwargs, campo):
    with pytest.raises(PacienteInvalido) as erro:
        Paciente(**kwargs)
    assert erro.value.campo == campo
