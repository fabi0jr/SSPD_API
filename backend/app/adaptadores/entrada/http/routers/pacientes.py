from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import AwareDatetime

from app.adaptadores.entrada.http.deps import cadastrar_paciente, consultas
from app.adaptadores.entrada.http.schemas import (
    Erro,
    LeituraSaida,
    PacienteEntrada,
    PacienteSaida,
    ResumoPacienteSaida,
)
from app.adaptadores.entrada.http.seguranca import exigir_jwt
from app.aplicacao.cadastrar_paciente import CadastrarPaciente
from app.aplicacao.consultas import LIMITE_MAXIMO, LIMITE_PADRAO, Consultas

ERRO_401 = {401: {"model": Erro, "description": "Token JWT inválido, expirado ou ausente"}}
ERRO_404 = {404: {"model": Erro, "description": "Paciente não encontrado"}}

router = APIRouter(prefix="/pacientes", tags=["pacientes"], dependencies=[Depends(exigir_jwt)])

PacienteId = Path(max_length=10, description="Código do paciente, ex. P001", examples=["P001"])


@router.get(
    "",
    response_model=list[ResumoPacienteSaida],
    summary="Lista os pacientes com a última leitura e os alertas abertos",
    description="Ordenados por id. Tela principal do dashboard.",
    responses=ERRO_401,
)
def listar(c: Consultas = Depends(consultas)) -> list[ResumoPacienteSaida]:
    return [ResumoPacienteSaida.de_resumo(r) for r in c.listar_pacientes()]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=PacienteSaida,
    summary="Cadastra um paciente (dados fictícios)",
    responses={
        **ERRO_401,
        409: {"model": Erro, "description": "Já existe paciente com esse id"},
        422: {"model": Erro, "description": "Campo vazio, longo demais ou em formato inválido"},
    },
)
def cadastrar(
    corpo: PacienteEntrada, caso_de_uso: CadastrarPaciente = Depends(cadastrar_paciente)
) -> PacienteSaida:
    return PacienteSaida.de(caso_de_uso.executar(corpo.para_entidade()))


@router.get(
    "/{paciente_id}/ultima",
    response_model=LeituraSaida | None,
    summary="Última leitura do paciente",
    description="Responde `null` se o paciente existe mas ainda não mandou nenhuma leitura.",
    responses={**ERRO_401, **ERRO_404},
)
def ultima(paciente_id: str = PacienteId, c: Consultas = Depends(consultas)) -> LeituraSaida | None:
    leitura = c.ultima_leitura(paciente_id)
    return LeituraSaida.de(leitura) if leitura else None


@router.get(
    "/{paciente_id}/leituras",
    response_model=list[LeituraSaida],
    summary="Histórico de leituras do paciente",
    description="Ordenado por `ts` decrescente (mais recente primeiro). `desde` e `ate` são inclusivos.",
    responses={**ERRO_401, **ERRO_404},
)
def historico(
    paciente_id: str = PacienteId,
    desde: AwareDatetime | None = Query(None, description="ISO 8601 com fuso", examples=["2026-11-25T13:00:00Z"]),
    ate: AwareDatetime | None = Query(None, description="ISO 8601 com fuso", examples=["2026-11-25T15:00:00Z"]),
    limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    c: Consultas = Depends(consultas),
) -> list[LeituraSaida]:
    return [LeituraSaida.de(l) for l in c.historico(paciente_id, desde, ate, limit)]
