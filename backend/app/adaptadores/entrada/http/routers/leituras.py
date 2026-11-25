from fastapi import APIRouter, Depends, Response, status

from app.adaptadores.entrada.http.deps import registrar_leitura
from app.adaptadores.entrada.http.schemas import Erro, LeituraEntrada, LeituraRegistrada
from app.adaptadores.entrada.http.seguranca import exigir_api_key
from app.aplicacao.registrar_leitura import RegistrarLeitura

router = APIRouter(tags=["leituras"])


@router.post(
    "/leituras",
    status_code=status.HTTP_201_CREATED,
    response_model=LeituraRegistrada,
    dependencies=[Depends(exigir_api_key)],
    summary="Registra uma leitura de sensor (idempotente)",
    description=(
        "Grava a leitura. Reenviar o mesmo `id` não duplica: responde 200 em vez de 201. "
        "O simulador trata qualquer 2xx como sucesso."
    ),
    responses={
        200: {"model": LeituraRegistrada, "description": "Leitura já existia (reenvio do buffer)"},
        401: {"model": Erro, "description": "API key inválida ou ausente"},
        404: {"model": Erro, "description": "Paciente não encontrado"},
        422: {"model": Erro, "description": "Valor fora da faixa, ts mais de 5 min no futuro ou formato inválido"},
    },
)
def registrar(
    corpo: LeituraEntrada,
    response: Response,
    caso_de_uso: RegistrarLeitura = Depends(registrar_leitura),
) -> LeituraRegistrada:
    leitura = corpo.para_entidade()
    nova = caso_de_uso.executar(leitura)
    if not nova:
        response.status_code = status.HTTP_200_OK
    return LeituraRegistrada(id=leitura.id, nova=nova)
