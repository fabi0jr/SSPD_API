"""Testes unitários do simulador, sem rede: o envio é uma função fake."""

import random
import re
import uuid

import pytest

import simulador as sim
from simulador import (
    DUPLICADA,
    FALHA,
    NOVA,
    PERFIS,
    REJEITADA,
    ConfiguracaoInvalida,
    Estatisticas,
    Resposta,
    Sensor,
    classificar,
    ler_configuracao,
)


class EnvioFake:
    """Responde com os status da lista, em ordem (o último se repete)."""

    def __init__(self, *status):
        self.status = list(status)
        self.recebidas = []

    def __call__(self, leitura):
        self.recebidas.append(leitura)
        status = self.status.pop(0) if len(self.status) > 1 else self.status[0]
        return Resposta(status)


def criar_sensor(envio, *, perfil="estavel", tamanho_buffer=1000, semente=1):
    return Sensor("P001", PERFIS[perfil], envio, Estatisticas(),
                  rng=random.Random(semente), tamanho_buffer=tamanho_buffer)


# ------------------------------------------------------------ classificar --

@pytest.mark.parametrize("status, esperado", [
    (201, NOVA),
    (200, DUPLICADA),
    (401, REJEITADA),
    (404, REJEITADA),
    (422, REJEITADA),
    (408, FALHA),
    (429, FALHA),
    (500, FALHA),
    (502, FALHA),
    (503, FALHA),
    (None, FALHA),
])
def test_classificar(status, esperado):
    assert classificar(status) == esperado


# ----------------------------------------------------------------- gerar ---

def test_leitura_tem_formato_do_contrato():
    leitura = criar_sensor(EnvioFake(201)).gerar()
    assert set(leitura) == {"id", "paciente_id", "fc", "spo2", "temp", "ts"}
    assert uuid.UUID(leitura["id"]).version == 4
    assert leitura["paciente_id"] == "P001"
    assert isinstance(leitura["fc"], int)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", leitura["ts"])


def test_cada_leitura_tem_id_novo():
    sensor = criar_sensor(EnvioFake(201))
    ids = {sensor.gerar()["id"] for _ in range(100)}
    assert len(ids) == 100


@pytest.mark.parametrize("perfil", list(PERFIS))
def test_valores_ficam_dentro_das_faixas_da_api(perfil):
    sensor = criar_sensor(EnvioFake(201), perfil=perfil)
    for _ in range(5000):
        leitura = sensor.gerar()
        assert 20 <= leitura["fc"] <= 250
        assert 50 <= leitura["spo2"] <= 100
        assert 30 <= leitura["temp"] <= 43


def test_anomalia_de_spo2_acontece_em_torno_de_2_por_cento():
    sensor = criar_sensor(EnvioFake(201), perfil="estavel")
    n = 10_000
    abaixo = sum(sensor.gerar()["spo2"] < 90 for _ in range(n))
    assert 0.01 * n < abaixo < 0.03 * n


def test_perfil_febril_gera_febre_e_estavel_nao():
    febril = criar_sensor(EnvioFake(201), perfil="febril")
    estavel = criar_sensor(EnvioFake(201), perfil="estavel")
    assert sum(febril.gerar()["temp"] >= 38 for _ in range(500)) > 250
    assert sum(estavel.gerar()["temp"] >= 38 for _ in range(500)) == 0


def test_distribuicao_de_perfis():
    assert sim.perfil_do_paciente(1).nome == "estavel"
    assert sim.perfil_do_paciente(3).nome == "febril"
    assert sim.perfil_do_paciente(7).nome == "hipoxemico"
    assert sim.codigo_paciente(7) == "P007"


# ------------------------------------------------------- buffer e retry ----

@pytest.mark.parametrize("status", [200, 201])
def test_2xx_tira_do_buffer(status):
    sensor = criar_sensor(EnvioFake(status))
    sensor.ciclo()
    assert len(sensor.buffer) == 0


def test_falha_mantem_no_buffer_e_reenvio_usa_o_mesmo_id():
    envio = EnvioFake(503, 503, 201)
    sensor = criar_sensor(envio)
    sensor.ciclo()                       # 503: fica no buffer
    assert len(sensor.buffer) == 1
    sensor.esvaziar()                    # 503 de novo
    sensor.esvaziar()                    # 201
    assert len(sensor.buffer) == 0
    assert len({leitura["id"] for leitura in envio.recebidas}) == 1


def test_buffer_e_reenviado_em_ordem_depois_da_queda():
    envio = EnvioFake(None)              # API fora
    sensor = criar_sensor(envio)
    for _ in range(5):
        sensor.ciclo()
    ids_gerados = [leitura["id"] for leitura in sensor.buffer]
    assert len(ids_gerados) == 5

    envio.status = [201]                 # API voltou
    envio.recebidas.clear()
    sensor.esvaziar()
    assert [leitura["id"] for leitura in envio.recebidas] == ids_gerados
    assert len(sensor.buffer) == 0


def test_esvaziar_para_na_primeira_falha():
    envio = EnvioFake(None)
    sensor = criar_sensor(envio)
    for _ in range(3):
        sensor.enfileirar(sensor.gerar())
    sensor.esvaziar()
    assert len(envio.recebidas) == 1     # não insiste nas outras
    assert len(sensor.buffer) == 3


def test_rejeitada_e_descartada_e_nao_trava_o_buffer():
    envio = EnvioFake(422, 201)
    sensor = criar_sensor(envio)
    sensor.enfileirar(sensor.gerar())
    sensor.enfileirar(sensor.gerar())
    sensor.esvaziar()
    assert len(sensor.buffer) == 0
    assert sensor.estatisticas.contagem[REJEITADA] == 1
    assert sensor.estatisticas.contagem[NOVA] == 1


def test_buffer_cheio_descarta_a_mais_antiga_e_conta_perda():
    sensor = criar_sensor(EnvioFake(None), tamanho_buffer=3)
    leituras = [sensor.gerar() for _ in range(5)]
    for leitura in leituras:
        sensor.enfileirar(leitura)
    assert [l["id"] for l in sensor.buffer] == [l["id"] for l in leituras[2:]]
    assert sensor.estatisticas.perdidas == 2


def test_esvaziar_respeita_o_pedido_de_parada():
    import threading

    parar = threading.Event()
    parar.set()
    envio = EnvioFake(201)
    sensor = criar_sensor(envio)
    sensor.enfileirar(sensor.gerar())
    sensor.esvaziar(parar)
    assert envio.recebidas == []
    assert len(sensor.buffer) == 1


# ---------------------------------------------------------- estatísticas ---

def test_estatisticas_detecta_queda_e_volta_da_api(caplog):
    caplog.set_level("INFO", logger="simulador")
    est = Estatisticas()
    est.registrar("P001", FALHA, Resposta(None, "ConnectionRefusedError"))
    est.registrar("P002", FALHA, Resposta(503))        # já estava fora: não loga de novo
    est.registrar("P001", NOVA, Resposta(201))
    mensagens = [r.getMessage() for r in caplog.records]
    assert sum("API indisponível" in m for m in mensagens) == 1
    assert sum("API disponível novamente" in m for m in mensagens) == 1
    assert est.resumo(buffer_total=7) == (
        "enviadas=1 novas=1 duplicadas=0 rejeitadas=0 falhas=2 perdidas=0 buffer=7")


# --------------------------------------------------------- configuração ----

def test_configuracao_com_padroes():
    cfg = ler_configuracao({"API_URL": "http://api/leituras", "SENSOR_API_KEY": "k"})
    assert (cfg.n_pacientes, cfg.intervalo) == (10, 2.0)


def test_configuracao_aceita_intervalo_fracionario():
    cfg = ler_configuracao({"API_URL": "u", "SENSOR_API_KEY": "k", "N_PACIENTES": "3", "INTERVALO": "0.5"})
    assert (cfg.n_pacientes, cfg.intervalo) == (3, 0.5)


@pytest.mark.parametrize("ambiente", [
    {"SENSOR_API_KEY": "k"},
    {"API_URL": "u"},
    {"API_URL": "u", "SENSOR_API_KEY": "k", "N_PACIENTES": "0"},
    {"API_URL": "u", "SENSOR_API_KEY": "k", "N_PACIENTES": "dez"},
    {"API_URL": "u", "SENSOR_API_KEY": "k", "INTERVALO": "-1"},
])
def test_configuracao_invalida(ambiente):
    with pytest.raises(ConfiguracaoInvalida):
        ler_configuracao(ambiente)
