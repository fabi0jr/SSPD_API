from fastapi import APIRouter, Depends, Response, status

from app.adaptadores.entrada.http.deps import Componentes, componentes
from app.adaptadores.entrada.http.schemas import Saude

router = APIRouter(tags=["saúde"])


@router.get(
    "/health",
    response_model=Saude,
    summary="Verifica se a API e o banco estão no ar",
    description="Executa SELECT 1 no banco. Usado pelo healthcheck do Compose e pelo smoke test do deploy.",
    responses={
        503: {
            "model": Saude,
            "description": "Banco indisponível",
            "content": {"application/json": {"example": {"status": "degradado", "db": "indisponivel"}}},
        }
    },
)
def health(response: Response, c: Componentes = Depends(componentes)) -> Saude:
    if c.verificar_banco():
        return Saude(status="ok", db="ok")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Saude(status="degradado", db="indisponivel")
