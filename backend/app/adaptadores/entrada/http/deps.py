"""Dependências do FastAPI que entregam os casos de uso às rotas.

As implementações concretas chegam prontas de composicao.py (ou dos testes,
com fakes) dentro de `Componentes`, guardado em app.state.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Request

from app.aplicacao.autenticar import Autenticar
from app.aplicacao.cadastrar_paciente import CadastrarPaciente
from app.aplicacao.consultas import Consultas
from app.aplicacao.reconhecer_alerta import ReconhecerAlerta
from app.aplicacao.registrar_leitura import RegistrarLeitura


@dataclass(frozen=True, kw_only=True)
class Componentes:
    registrar_leitura: RegistrarLeitura
    autenticar: Autenticar
    cadastrar_paciente: CadastrarPaciente
    consultas: Consultas
    reconhecer_alerta: ReconhecerAlerta
    verificar_banco: Callable[[], bool]


def componentes(request: Request) -> Componentes:
    return request.app.state.componentes


def registrar_leitura(c: Componentes = Depends(componentes)) -> RegistrarLeitura:
    return c.registrar_leitura


def autenticar(c: Componentes = Depends(componentes)) -> Autenticar:
    return c.autenticar


def cadastrar_paciente(c: Componentes = Depends(componentes)) -> CadastrarPaciente:
    return c.cadastrar_paciente


def consultas(c: Componentes = Depends(componentes)) -> Consultas:
    return c.consultas


def reconhecer_alerta(c: Componentes = Depends(componentes)) -> ReconhecerAlerta:
    return c.reconhecer_alerta
