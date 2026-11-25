from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.aplicacao.registrar_leitura import RegistrarLeitura
from app.dominio.entidades import Leitura, Paciente
from app.dominio.erros import LeituraInvalida, PacienteNaoEncontrado
from tests.fakes import BancoFake, RelogioFake, RepositorioLeiturasFake, RepositorioPacientesFake

AGORA = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


def leitura(**kwargs) -> Leitura:
    dados = {"id": uuid4(), "paciente_id": "P001", "fc": 80, "spo2": 97.0, "temp": 36.5, "ts": AGORA}
    dados.update(kwargs)
    return Leitura(**dados)


@pytest.fixture
def banco() -> BancoFake:
    banco = BancoFake()
    RepositorioPacientesFake(banco).criar(Paciente(id="P001", nome="Paciente Fictício 1"))
    return banco


@pytest.fixture
def caso_de_uso(banco) -> RegistrarLeitura:
    return RegistrarLeitura(
        pacientes=RepositorioPacientesFake(banco),
        leituras=RepositorioLeiturasFake(banco),
        relogio=RelogioFake(AGORA),
    )


def test_leitura_nova_devolve_true_e_grava(caso_de_uso, banco):
    l = leitura()
    assert caso_de_uso.executar(l) is True
    assert list(banco.leituras) == [l.id]
    assert banco.leituras[l.id].processada is False


def test_reenvio_do_mesmo_id_devolve_false_sem_duplicar(caso_de_uso, banco):
    l = leitura()
    caso_de_uso.executar(l)
    assert caso_de_uso.executar(l) is False
    assert len(banco.leituras) == 1


def test_reenvio_com_valores_diferentes_mantem_a_original(caso_de_uso, banco):
    original = leitura(fc=80)
    caso_de_uso.executar(original)
    assert caso_de_uso.executar(leitura(id=original.id, fc=150)) is False
    assert banco.leituras[original.id].fc == 80


def test_paciente_inexistente(caso_de_uso, banco):
    with pytest.raises(PacienteNaoEncontrado) as erro:
        caso_de_uso.executar(leitura(paciente_id="P999"))
    assert erro.value.paciente_id == "P999"
    assert banco.leituras == {}


def test_ts_mais_de_5_min_no_futuro(caso_de_uso, banco):
    with pytest.raises(LeituraInvalida) as erro:
        caso_de_uso.executar(leitura(ts=AGORA + timedelta(minutes=6)))
    assert erro.value.campo == "ts"
    assert banco.leituras == {}


def test_leitura_antiga_do_buffer_e_aceita(caso_de_uso):
    assert caso_de_uso.executar(leitura(ts=AGORA - timedelta(hours=3))) is True


def test_usa_o_relogio_injetado(banco):
    relogio = RelogioFake(AGORA)
    caso_de_uso = RegistrarLeitura(RepositorioPacientesFake(banco), RepositorioLeiturasFake(banco), relogio)
    futura = leitura(ts=AGORA + timedelta(minutes=10))

    with pytest.raises(LeituraInvalida):
        caso_de_uso.executar(futura)

    relogio.agora = AGORA + timedelta(minutes=6)  # o relógio do servidor "alcançou" o sensor
    assert caso_de_uso.executar(futura) is True
