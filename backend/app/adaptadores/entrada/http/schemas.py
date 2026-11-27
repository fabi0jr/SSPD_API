"""Schemas Pydantic (só aqui). Validam tipo e formato; as faixas são do domínio.

Os `examples` aparecem no Swagger, que o dashboard usa como contrato.
"""

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.dominio.entidades import (
    Alerta,
    Leitura,
    Paciente,
    ResumoPaciente,
    Severidade,
    StatusAlerta,
    TipoAlerta,
)


def _iso_utc(valor: datetime) -> str:
    """Sempre em UTC com 'Z', qualquer que seja o fuso da sessão do banco."""
    return valor.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# Timestamp de saída: ISO 8601 UTC (contrato da API).
DataHoraUtc = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str)]

EXEMPLO_LEITURA = {
    "id": "6f1c2e7a-3b2d-4c4e-9a51-0b8f3a1d2c77",
    "paciente_id": "P001",
    "fc": 82,
    "spo2": 97.4,
    "temp": 36.7,
    "ts": "2026-11-25T14:03:12.481Z",
}


class LeituraEntrada(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [EXEMPLO_LEITURA]})

    id: UUID = Field(description="UUID gerado pelo sensor no momento da medição (idempotência)")
    paciente_id: str = Field(min_length=1, max_length=10, description="Código do paciente, ex. P001")
    fc: int = Field(description="Frequência cardíaca (bpm), aceita de 20 a 250")
    spo2: float = Field(description="Saturação de O2 (%), aceita de 50 a 100")
    temp: float = Field(description="Temperatura (°C), aceita de 30 a 43")
    ts: AwareDatetime = Field(
        description="Hora da medição em ISO 8601 com fuso (Z ou offset); no máximo 5 min no futuro"
    )

    def para_entidade(self) -> Leitura:
        """Levanta LeituraInvalida se algum valor estiver fora da faixa."""
        return Leitura(
            id=self.id,
            paciente_id=self.paciente_id,
            fc=self.fc,
            spo2=self.spo2,
            temp=self.temp,
            ts=self.ts,
        )


class LeituraRegistrada(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"id": EXEMPLO_LEITURA["id"], "nova": True}]}
    )

    id: UUID
    nova: bool = Field(description="false quando o id já existia (reenvio do buffer do sensor)")


class LeituraSaida(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    **EXEMPLO_LEITURA,
                    "recebido_em": "2026-11-25T14:03:12.530Z",
                    "processada": True,
                }
            ]
        }
    )

    id: UUID
    paciente_id: str
    fc: int
    spo2: float
    temp: float
    ts: DataHoraUtc
    recebido_em: DataHoraUtc | None = Field(description="Hora de chegada no servidor; recebido_em - ts = latência")
    processada: bool = Field(description="false enquanto o worker de alertas não avaliou a leitura")

    @classmethod
    def de(cls, l: Leitura) -> "LeituraSaida":
        return cls(
            id=l.id, paciente_id=l.paciente_id, fc=l.fc, spo2=l.spo2, temp=l.temp,
            ts=l.ts, recebido_em=l.recebido_em, processada=l.processada,
        )


EXEMPLO_PACIENTE = {"id": "P051", "nome": "Paciente Fictício 051", "leito": "L-051"}


class PacienteEntrada(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [EXEMPLO_PACIENTE]})

    id: str = Field(min_length=1, max_length=10, description="Código do paciente, ex. P051 (o mesmo usado pelo sensor)")
    nome: str = Field(min_length=1, max_length=100, description="Nome fictício (LGPD)")
    leito: str | None = Field(default=None, max_length=20)

    def para_entidade(self) -> Paciente:
        """Levanta PacienteInvalido (ex.: nome só com espaços)."""
        return Paciente(id=self.id, nome=self.nome, leito=self.leito)


class PacienteSaida(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{**EXEMPLO_PACIENTE, "criado_em": "2026-11-25T13:00:00Z"}]}
    )

    id: str
    nome: str
    leito: str | None
    criado_em: DataHoraUtc | None

    @classmethod
    def de(cls, p: Paciente) -> "PacienteSaida":
        return cls(id=p.id, nome=p.nome, leito=p.leito, criado_em=p.criado_em)


class ResumoPacienteSaida(PacienteSaida):
    """Linha de GET /pacientes."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "P001",
                    "nome": "Paciente Fictício 001",
                    "leito": "L-001",
                    "criado_em": "2026-11-25T13:00:00Z",
                    "ultima_leitura": {
                        **EXEMPLO_LEITURA,
                        "recebido_em": "2026-11-25T14:03:12.530Z",
                        "processada": True,
                    },
                    "alertas_abertos": 1,
                }
            ]
        }
    )

    ultima_leitura: LeituraSaida | None = Field(description="null se o paciente ainda não tem leituras")
    alertas_abertos: int

    @classmethod
    def de_resumo(cls, r: ResumoPaciente) -> "ResumoPacienteSaida":
        p = r.paciente
        return cls(
            id=p.id, nome=p.nome, leito=p.leito, criado_em=p.criado_em,
            ultima_leitura=LeituraSaida.de(r.ultima_leitura) if r.ultima_leitura else None,
            alertas_abertos=r.alertas_abertos,
        )


class AlertaSaida(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 42,
                    "paciente_id": "P001",
                    "leitura_id": EXEMPLO_LEITURA["id"],
                    "tipo": "SPO2_BAIXA",
                    "valor": 87.5,
                    "severidade": "ALTA",
                    "status": "aberto",
                    "criado_em": "2026-11-25T14:03:14.002Z",
                }
            ]
        }
    )

    id: int
    paciente_id: str
    leitura_id: UUID
    tipo: TipoAlerta
    valor: float
    severidade: Severidade
    status: StatusAlerta
    criado_em: DataHoraUtc | None

    @classmethod
    def de(cls, a: Alerta) -> "AlertaSaida":
        return cls(
            id=a.id, paciente_id=a.paciente_id, leitura_id=a.leitura_id, tipo=a.tipo,
            valor=a.valor, severidade=a.severidade, status=a.status, criado_em=a.criado_em,
        )


class AlertaAtualizacao(BaseModel):
    """Só o reconhecimento existe. Reabrir não é permitido: um alerta novo é criado
    pelo worker se a leitura voltar a ficar alterada."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "reconhecido"}]})

    status: Literal["reconhecido"]


class Login(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"email": "admin@example.com", "senha": "troque-esta-senha"}]}
    )

    email: str = Field(min_length=3, max_length=120)
    senha: str = Field(min_length=1, max_length=72, description="O bcrypt usa no máximo 72 bytes")


# Limite do bcrypt: só os primeiros 72 bytes contam (e o bcrypt 5 recusa senha maior).
MAXIMO_BYTES_SENHA = 72


class Cadastro(BaseModel):
    """Mesmo `email` do Login (mesmos limites, sem normalização de caixa nem de
    espaços), para que quem se cadastrou consiga logar com o mesmo texto. O
    cadastro só acrescenta uma checagem de formato."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"email": "fulano@exemplo.com", "senha": "senha-segura-123"}]}
    )

    email: str = Field(
        min_length=3,
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        description="E-mail usado no login, exatamente como digitado (maiúsculas contam)",
    )
    senha: str = Field(min_length=8, description="De 8 caracteres a 72 bytes em UTF-8 (limite do bcrypt)")

    @field_validator("senha")
    @classmethod
    def _senha_cabe_no_bcrypt(cls, senha: str) -> str:
        if len(senha.encode("utf-8")) > MAXIMO_BYTES_SENHA:
            raise ValueError(f"a senha pode ter no máximo {MAXIMO_BYTES_SENHA} bytes em UTF-8")
        return senha


class UsuarioSaida(BaseModel):
    """Resposta do cadastro: nunca leva a senha nem o hash."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"id": 2, "email": "fulano@exemplo.com"}]})

    id: int
    email: str


class Token(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"access_token": "eyJhbGciOiJIUzI1NiIs...", "token_type": "bearer"}]}
    )

    access_token: str
    token_type: Literal["bearer"] = "bearer"


class Saude(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok", "db": "ok"}]})

    status: Literal["ok", "degradado"]
    db: Literal["ok", "indisponivel"]


class Erro(BaseModel):
    """Formato padrão do FastAPI: {"detail": ...}."""

    detail: str | list[dict]
