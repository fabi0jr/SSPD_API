"""Montagem da API com fakes, compartilhada pelos testes HTTP unitários."""

from datetime import datetime, timezone
from functools import partial

from app.adaptadores.entrada.http.deps import Componentes
from app.adaptadores.entrada.http.main import criar_app
from app.adaptadores.entrada.http.seguranca import ConfigJwt, gerar_hash_senha, verificar_senha
from app.aplicacao.autenticar import Autenticar
from app.aplicacao.cadastrar_paciente import CadastrarPaciente
from app.aplicacao.cadastrar_usuario import CadastrarUsuario
from app.aplicacao.consultas import Consultas
from app.aplicacao.reconhecer_alerta import ReconhecerAlerta
from app.aplicacao.registrar_leitura import RegistrarLeitura
from tests.fakes import (
    BancoFake,
    RelogioFake,
    RepositorioAlertasFake,
    RepositorioLeiturasFake,
    RepositorioPacientesFake,
    RepositorioUsuariosFake,
)

AGORA = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)
CHAVE = "chave-de-teste"
ORIGEM_DASHBOARD = "https://dashboard.exemplo"
JWT = ConfigJwt(segredo="segredo-de-teste-com-pelo-menos-32-bytes", expiracao_minutos=60)


class Saude:
    """verificar_banco controlável pelo teste."""

    def __init__(self) -> None:
        self.no_ar = True

    def __call__(self) -> bool:
        return self.no_ar


def montar_componentes(banco: BancoFake, saude: Saude, **sobrescritas) -> Componentes:
    """Componentes com fakes; `sobrescritas` troca um caso de uso (ex.: um que quebra)."""
    pacientes = RepositorioPacientesFake(banco)
    leituras = RepositorioLeiturasFake(banco)
    alertas = RepositorioAlertasFake(banco)
    usuarios = RepositorioUsuariosFake(banco)
    padrao = dict(
        registrar_leitura=RegistrarLeitura(pacientes, leituras, RelogioFake(AGORA)),
        autenticar=Autenticar(usuarios, verificar_senha),
        # bcrypt de verdade, com rounds=4 (o mínimo) para o teste ser rápido.
        cadastrar_usuario=CadastrarUsuario(usuarios, partial(gerar_hash_senha, rounds=4)),
        cadastrar_paciente=CadastrarPaciente(pacientes),
        consultas=Consultas(pacientes, leituras, alertas),
        reconhecer_alerta=ReconhecerAlerta(alertas),
        verificar_banco=saude,
    )
    return Componentes(**(padrao | sobrescritas))


def criar_app_fake(componentes: Componentes, **kwargs):
    return criar_app(
        componentes,
        sensor_api_key=CHAVE,
        jwt=JWT,
        cors_origins=[ORIGEM_DASHBOARD],
        **kwargs,
    )
