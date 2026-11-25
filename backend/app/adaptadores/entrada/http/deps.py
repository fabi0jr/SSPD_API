"""Dependências do FastAPI que entregam os casos de uso às rotas.

As implementações concretas chegam prontas de composicao.py (ou dos testes,
com fakes) dentro de `Componentes`, guardado em app.state.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Request

from app.aplicacao.registrar_leitura import RegistrarLeitura


@dataclass(frozen=True)
class Componentes:
    registrar_leitura: RegistrarLeitura
    verificar_banco: Callable[[], bool]


def componentes(request: Request) -> Componentes:
    return request.app.state.componentes


def registrar_leitura(c: Componentes = Depends(componentes)) -> RegistrarLeitura:
    return c.registrar_leitura
