from fastapi import APIRouter, Depends, Path, Query

from app.adaptadores.entrada.http.deps import consultas, reconhecer_alerta
from app.adaptadores.entrada.http.schemas import AlertaAtualizacao, AlertaSaida, Erro
from app.adaptadores.entrada.http.seguranca import exigir_jwt
from app.aplicacao.consultas import LIMITE_MAXIMO, LIMITE_PADRAO, Consultas
from app.aplicacao.reconhecer_alerta import ReconhecerAlerta
from app.dominio.entidades import StatusAlerta

ERRO_401 = {401: {"model": Erro, "description": "Token JWT inválido, expirado ou ausente"}}

router = APIRouter(prefix="/alertas", tags=["alertas"], dependencies=[Depends(exigir_jwt)])


@router.get(
    "",
    response_model=list[AlertaSaida],
    summary="Lista os alertas, mais recentes primeiro",
    description="Sem `status`, traz abertos e reconhecidos.",
    responses=ERRO_401,
)
def listar(
    status: StatusAlerta | None = Query(None, examples=["aberto"]),
    paciente_id: str | None = Query(None, max_length=10, examples=["P001"]),
    limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    c: Consultas = Depends(consultas),
) -> list[AlertaSaida]:
    return [AlertaSaida.de(a) for a in c.listar_alertas(status, paciente_id, limit)]


@router.patch(
    "/{alerta_id}",
    response_model=AlertaSaida,
    summary="Reconhece um alerta (idempotente)",
    description=(
        "Só aceita `{\"status\": \"reconhecido\"}`. Reconhecer de novo responde 200. "
        "Depois de reconhecido, uma nova leitura alterada do mesmo tipo gera um alerta novo."
    ),
    responses={
        **ERRO_401,
        404: {"model": Erro, "description": "Alerta não encontrado"},
        422: {"model": Erro, "description": "Status diferente de 'reconhecido'"},
    },
)
def reconhecer(
    corpo: AlertaAtualizacao,
    alerta_id: int = Path(ge=1),
    caso_de_uso: ReconhecerAlerta = Depends(reconhecer_alerta),
) -> AlertaSaida:
    return AlertaSaida.de(caso_de_uso.executar(alerta_id))
