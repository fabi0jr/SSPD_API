"""Regras de alerta. Funções puras: recebem uma leitura e devolvem alertas.

O anti-spam (não duplicar um alerta aberto) não fica aqui, porque depende
de estado. Ele fica no caso de uso ProcessarPendentes.
"""

from app.dominio.entidades import Alerta, Leitura, Severidade, TipoAlerta

LIMITE_SPO2_BAIXA = 90.0     # spo2 <  90    -> SPO2_BAIXA
LIMITE_TAQUICARDIA = 120     # fc   >  120   -> TAQUICARDIA
LIMITE_BRADICARDIA = 50      # fc   <  50    -> BRADICARDIA
LIMITE_FEBRE = 38.0          # temp >= 38.0  -> FEBRE
LIMITE_HIPOTERMIA = 35.0     # temp <  35.0  -> HIPOTERMIA


def _alerta(leitura: Leitura, tipo: TipoAlerta, valor: float, severidade: Severidade) -> Alerta:
    return Alerta(
        paciente_id=leitura.paciente_id,
        leitura_id=leitura.id,
        tipo=tipo,
        valor=valor,
        severidade=severidade,
    )


def avaliar(leitura: Leitura) -> list[Alerta]:
    """Devolve os alertas que a leitura dispara, em ordem fixa (SpO2, FC, temperatura)."""
    alertas: list[Alerta] = []

    if leitura.spo2 < LIMITE_SPO2_BAIXA:
        alertas.append(_alerta(leitura, TipoAlerta.SPO2_BAIXA, leitura.spo2, Severidade.ALTA))

    if leitura.fc > LIMITE_TAQUICARDIA:
        alertas.append(_alerta(leitura, TipoAlerta.TAQUICARDIA, leitura.fc, Severidade.MEDIA))
    elif leitura.fc < LIMITE_BRADICARDIA:
        alertas.append(_alerta(leitura, TipoAlerta.BRADICARDIA, leitura.fc, Severidade.MEDIA))

    if leitura.temp >= LIMITE_FEBRE:
        alertas.append(_alerta(leitura, TipoAlerta.FEBRE, leitura.temp, Severidade.MEDIA))
    elif leitura.temp < LIMITE_HIPOTERMIA:
        alertas.append(_alerta(leitura, TipoAlerta.HIPOTERMIA, leitura.temp, Severidade.ALTA))

    return alertas
