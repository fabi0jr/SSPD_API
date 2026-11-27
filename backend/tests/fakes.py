"""Implementações em memória das portas, para os testes unitários.

Todos os repositórios compartilham um `BancoFake`, como as tabelas de um banco.
Os fakes seguem o contrato das portas (ver tests/unit/test_fakes.py), em especial:
- salvar_se_nova é idempotente;
- FilaPendentesFake.reservar só aplica as mudanças se o `with` terminar sem
  exceção (simula COMMIT/ROLLBACK) e pula leituras já reservadas (SKIP LOCKED).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from uuid import UUID

from app.dominio.entidades import (
    Alerta,
    Leitura,
    Paciente,
    ResumoPaciente,
    StatusAlerta,
    TipoAlerta,
    Usuario,
)
from app.dominio.erros import PacienteJaExiste, UsuarioJaExiste


def _agora() -> datetime:
    return datetime.now(timezone.utc)


class RelogioFake:
    """Relógio controlável: `relogio()` devolve `relogio.agora`."""

    def __init__(self, agora: datetime) -> None:
        self.agora = agora

    def __call__(self) -> datetime:
        return self.agora


@dataclass
class BancoFake:
    pacientes: dict[str, Paciente] = field(default_factory=dict)
    leituras: dict[UUID, Leitura] = field(default_factory=dict)
    alertas: dict[int, Alerta] = field(default_factory=dict)
    usuarios: dict[str, Usuario] = field(default_factory=dict)
    _proximo_alerta_id: int = 1

    def inserir_alerta(self, alerta: Alerta) -> Alerta:
        """Grava como o BIGSERIAL faria: atribui id e criado_em."""
        gravado = replace(
            alerta,
            id=self._proximo_alerta_id,
            criado_em=alerta.criado_em or _agora(),
        )
        self.alertas[gravado.id] = gravado
        self._proximo_alerta_id += 1
        return replace(gravado)

    def pendentes(self) -> list[Leitura]:
        return [l for l in self.leituras.values() if not l.processada]


class RepositorioPacientesFake:
    def __init__(self, banco: BancoFake) -> None:
        self._banco = banco

    def existe(self, paciente_id: str) -> bool:
        return paciente_id in self._banco.pacientes

    def obter(self, paciente_id: str) -> Paciente | None:
        return self._banco.pacientes.get(paciente_id)

    def listar_com_resumo(self) -> list[ResumoPaciente]:
        leituras = RepositorioLeiturasFake(self._banco)
        resumos = []
        for paciente in sorted(self._banco.pacientes.values(), key=lambda p: p.id):
            abertos = sum(
                1
                for a in self._banco.alertas.values()
                if a.paciente_id == paciente.id and a.status == StatusAlerta.ABERTO
            )
            resumos.append(ResumoPaciente(paciente, leituras.ultima(paciente.id), abertos))
        return resumos

    def criar(self, paciente: Paciente) -> None:
        if paciente.id in self._banco.pacientes:
            raise PacienteJaExiste(paciente.id)
        self._banco.pacientes[paciente.id] = replace(paciente, criado_em=paciente.criado_em or _agora())


class RepositorioLeiturasFake:
    def __init__(self, banco: BancoFake) -> None:
        self._banco = banco

    def salvar_se_nova(self, leitura: Leitura) -> bool:
        if leitura.id in self._banco.leituras:
            return False
        self._banco.leituras[leitura.id] = replace(
            leitura, recebido_em=leitura.recebido_em or _agora()
        )
        return True

    def ultima(self, paciente_id: str) -> Leitura | None:
        historico = self.historico(paciente_id, None, None, 1)
        return historico[0] if historico else None

    def historico(
        self,
        paciente_id: str,
        desde: datetime | None,
        ate: datetime | None,
        limite: int,
    ) -> list[Leitura]:
        selecionadas = [
            l
            for l in self._banco.leituras.values()
            if l.paciente_id == paciente_id
            and (desde is None or l.ts >= desde)
            and (ate is None or l.ts <= ate)
        ]
        selecionadas.sort(key=lambda l: l.ts, reverse=True)
        return selecionadas[:limite]


class RepositorioAlertasFake:
    def __init__(self, banco: BancoFake) -> None:
        self._banco = banco

    def listar(
        self,
        status: StatusAlerta | None,
        paciente_id: str | None,
        limite: int,
    ) -> list[Alerta]:
        selecionados = [
            replace(a)
            for a in self._banco.alertas.values()
            if (status is None or a.status == status)
            and (paciente_id is None or a.paciente_id == paciente_id)
        ]
        selecionados.sort(key=lambda a: (a.criado_em, a.id), reverse=True)
        return selecionados[:limite]

    def obter(self, alerta_id: int) -> Alerta | None:
        alerta = self._banco.alertas.get(alerta_id)
        return replace(alerta) if alerta else None

    def atualizar_status(self, alerta_id: int, status: StatusAlerta) -> None:
        # Como um UPDATE: alerta inexistente não afeta nenhuma linha e não dá erro.
        if alerta_id in self._banco.alertas:
            self._banco.alertas[alerta_id].status = status


class RepositorioUsuariosFake:
    def __init__(self, banco: BancoFake) -> None:
        self._banco = banco

    def por_email(self, email: str) -> Usuario | None:
        return self._banco.usuarios.get(email)

    def criar(self, email: str, senha_hash: str) -> Usuario:
        # Como o UNIQUE de usuarios.email; o id segue o SERIAL (maior + 1).
        if email in self._banco.usuarios:
            raise UsuarioJaExiste(email)
        proximo_id = max((u.id or 0 for u in self._banco.usuarios.values()), default=0) + 1
        usuario = Usuario(id=proximo_id, email=email, senha_hash=senha_hash)
        self._banco.usuarios[email] = usuario
        return usuario


class ErroInjetado(Exception):
    """Falha simulada dentro de um lote (ex.: banco caiu no meio do ciclo)."""


class LotePendenteFake:
    """Acumula as mudanças. Só a FilaPendentesFake as aplica, no 'commit'."""

    def __init__(self, banco: BancoFake, leituras: list[Leitura], falhar_em: str | None) -> None:
        self._banco = banco
        self.leituras = leituras
        self._falhar_em = falhar_em
        self.alertas_pendentes: list[Alerta] = []
        self.marcou = False

    def _talvez_falhar(self, operacao: str) -> None:
        if self._falhar_em == operacao:
            raise ErroInjetado(operacao)

    def tipos_abertos(self, paciente_id: str) -> set[TipoAlerta]:
        self._talvez_falhar("tipos_abertos")
        # Dentro da transação, os alertas já registrados no lote também são visíveis.
        todos = [*self._banco.alertas.values(), *self.alertas_pendentes]
        return {
            a.tipo
            for a in todos
            if a.paciente_id == paciente_id and a.status == StatusAlerta.ABERTO
        }

    def registrar_alertas(self, alertas: list[Alerta]) -> None:
        self._talvez_falhar("registrar_alertas")
        self.alertas_pendentes.extend(alertas)

    def marcar_processadas(self) -> None:
        self._talvez_falhar("marcar_processadas")
        self.marcou = True


class FilaPendentesFake:
    def __init__(self, banco: BancoFake) -> None:
        self._banco = banco
        self._travadas: set[UUID] = set()
        # Nome do método do lote que deve levantar ErroInjetado (None = sem falha).
        self.falhar_em: str | None = None
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def reservar(self, limite: int) -> Iterator[LotePendenteFake]:
        livres = [l for l in self._banco.pendentes() if l.id not in self._travadas]
        livres.sort(key=lambda l: l.ts)
        reservadas = livres[:limite]
        ids = {l.id for l in reservadas}
        self._travadas |= ids

        lote = LotePendenteFake(self._banco, reservadas, self.falhar_em)
        try:
            yield lote
        except BaseException:
            self.rollbacks += 1
            raise
        else:
            for alerta in lote.alertas_pendentes:
                self._banco.inserir_alerta(alerta)
            if lote.marcou:
                for leitura_id in ids:
                    self._banco.leituras[leitura_id] = replace(
                        self._banco.leituras[leitura_id], processada=True
                    )
            self.commits += 1
        finally:
            self._travadas -= ids

    def contar_pendentes(self) -> int:
        return len(self._banco.pendentes())
