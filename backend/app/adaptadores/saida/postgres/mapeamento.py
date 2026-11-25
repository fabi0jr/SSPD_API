"""Conversão model SQLAlchemy <-> entidade de domínio.

NUMERIC volta do banco como Decimal; o domínio trabalha com float.
"""

from app.adaptadores.saida.postgres.models import (
    AlertaModel,
    LeituraModel,
    PacienteModel,
    UsuarioModel,
)
from app.dominio.entidades import (
    Alerta,
    Leitura,
    Paciente,
    Severidade,
    StatusAlerta,
    TipoAlerta,
    Usuario,
)


def paciente(m: PacienteModel) -> Paciente:
    return Paciente(id=m.id, nome=m.nome, leito=m.leito, criado_em=m.criado_em)


def leitura(m: LeituraModel) -> Leitura:
    return Leitura(
        id=m.id,
        paciente_id=m.paciente_id,
        fc=m.fc,
        spo2=float(m.spo2),
        temp=float(m.temp),
        ts=m.ts,
        recebido_em=m.recebido_em,
        processada=m.processada,
    )


def leitura_para_linha(l: Leitura) -> dict:
    """Colunas para INSERT. recebido_em e processada ficam com o default do banco."""
    return {
        "id": l.id,
        "paciente_id": l.paciente_id,
        "fc": l.fc,
        "spo2": l.spo2,
        "temp": l.temp,
        "ts": l.ts,
    }


def alerta(m: AlertaModel) -> Alerta:
    return Alerta(
        id=m.id,
        paciente_id=m.paciente_id,
        leitura_id=m.leitura_id,
        tipo=TipoAlerta(m.tipo),
        valor=float(m.valor),
        severidade=Severidade(m.severidade),
        status=StatusAlerta(m.status),
        criado_em=m.criado_em,
    )


def alerta_para_linha(a: Alerta) -> dict:
    """Colunas para INSERT. id e criado_em ficam com o default do banco."""
    return {
        "paciente_id": a.paciente_id,
        "leitura_id": a.leitura_id,
        "tipo": a.tipo.value,
        "valor": a.valor,
        "severidade": a.severidade.value,
        "status": a.status.value,
    }


def usuario(m: UsuarioModel) -> Usuario:
    return Usuario(id=m.id, email=m.email, senha_hash=m.senha_hash)
