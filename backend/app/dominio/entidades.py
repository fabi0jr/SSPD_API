"""Entidades do domínio. Só stdlib: nada de Pydantic, SQLAlchemy ou FastAPI aqui."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.dominio.erros import LeituraInvalida, PacienteInvalido

# Faixas fisiológicas aceitas (inclusivas). Fora delas a leitura é rejeitada (422):
# não é um alerta, é um sensor com defeito ou um dado corrompido.
FAIXA_FC = (20, 250)
FAIXA_SPO2 = (50.0, 100.0)
FAIXA_TEMP = (30.0, 43.0)

# Tolerância para relógio de sensor adiantado.
TOLERANCIA_FUTURO = timedelta(minutes=5)

TAMANHO_MAX_ID_PACIENTE = 10
TAMANHO_MAX_NOME_PACIENTE = 100


class TipoAlerta(StrEnum):
    SPO2_BAIXA = "SPO2_BAIXA"
    TAQUICARDIA = "TAQUICARDIA"
    BRADICARDIA = "BRADICARDIA"
    FEBRE = "FEBRE"
    HIPOTERMIA = "HIPOTERMIA"


class Severidade(StrEnum):
    ALTA = "ALTA"
    MEDIA = "MEDIA"


class StatusAlerta(StrEnum):
    ABERTO = "aberto"
    RECONHECIDO = "reconhecido"


def _checar_faixa(campo: str, valor: float, faixa: tuple[float, float]) -> None:
    minimo, maximo = faixa
    if not (minimo <= valor <= maximo):
        raise LeituraInvalida(campo, f"deve estar entre {minimo} e {maximo} (recebido {valor})")


@dataclass(frozen=True, slots=True)
class Leitura:
    id: UUID                          # gerado pelo sensor (idempotência)
    paciente_id: str
    fc: int                           # bpm
    spo2: float                       # %
    temp: float                       # °C
    ts: datetime                      # hora da medição, com fuso
    recebido_em: datetime | None = None   # preenchido pelo banco
    processada: bool = False

    def __post_init__(self) -> None:
        _checar_faixa("fc", self.fc, FAIXA_FC)
        _checar_faixa("spo2", self.spo2, FAIXA_SPO2)
        _checar_faixa("temp", self.temp, FAIXA_TEMP)
        if self.ts.tzinfo is None or self.ts.utcoffset() is None:
            raise LeituraInvalida("ts", "precisa ter fuso horário (ISO 8601 com Z ou offset)")

    def validar_horario(self, agora: datetime) -> None:
        """Rejeita `ts` mais de 5 min no futuro em relação a `agora`.

        Fica fora do __post_init__ porque depende do momento do recebimento:
        uma leitura antiga reenviada pelo buffer continua válida.
        """
        if self.ts > agora + TOLERANCIA_FUTURO:
            raise LeituraInvalida("ts", "está mais de 5 minutos no futuro")


@dataclass(slots=True)
class Alerta:
    paciente_id: str
    leitura_id: UUID
    tipo: TipoAlerta
    valor: float
    severidade: Severidade
    status: StatusAlerta = StatusAlerta.ABERTO
    id: int | None = None             # preenchido pelo banco
    criado_em: datetime | None = None  # preenchido pelo banco


@dataclass(frozen=True, slots=True)
class Paciente:
    id: str                           # "P001", mesmo código usado pelo simulador
    nome: str                         # fictício
    leito: str | None = None
    criado_em: datetime | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise PacienteInvalido("id", "não pode ser vazio")
        if len(self.id) > TAMANHO_MAX_ID_PACIENTE:
            raise PacienteInvalido("id", f"máximo de {TAMANHO_MAX_ID_PACIENTE} caracteres")
        if not self.nome or not self.nome.strip():
            raise PacienteInvalido("nome", "não pode ser vazio")
        if len(self.nome) > TAMANHO_MAX_NOME_PACIENTE:
            raise PacienteInvalido("nome", f"máximo de {TAMANHO_MAX_NOME_PACIENTE} caracteres")


@dataclass(frozen=True, slots=True)
class Usuario:
    id: int | None
    email: str
    senha_hash: str


@dataclass(frozen=True, slots=True)
class ResumoPaciente:
    """Linha de GET /pacientes: paciente + última leitura + alertas abertos."""

    paciente: Paciente
    ultima_leitura: Leitura | None
    alertas_abertos: int


@dataclass(frozen=True, slots=True)
class ResultadoCiclo:
    """Resultado de um ciclo do worker, usado nos logs (evidência do teste de falha)."""

    processadas: int
    alertas_criados: int
    backlog: int                      # pendentes restantes depois do commit
