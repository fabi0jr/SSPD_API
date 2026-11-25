"""Schemas Pydantic (só aqui). Validam tipo e formato; as faixas são do domínio.

Os `examples` aparecem no Swagger, que o dashboard usa como contrato.
"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.dominio.entidades import Leitura

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


class Saude(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok", "db": "ok"}]})

    status: Literal["ok", "degradado"]
    db: Literal["ok", "indisponivel"]


class Erro(BaseModel):
    """Formato padrão do FastAPI: {"detail": ...}."""

    detail: str | list[dict]
