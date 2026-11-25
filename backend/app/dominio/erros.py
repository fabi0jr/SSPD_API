"""Erros de domínio. O adaptador HTTP converte cada um em um status HTTP."""


class ErroDominio(Exception):
    """Base de todos os erros de domínio."""


class LeituraInvalida(ErroDominio):
    """Valor fora da faixa fisiológica aceita ou timestamp inválido (HTTP 422)."""

    def __init__(self, campo: str, mensagem: str) -> None:
        super().__init__(f"{campo}: {mensagem}")
        self.campo = campo
        self.mensagem = mensagem


class PacienteInvalido(ErroDominio):
    """Dados de cadastro de paciente inválidos (HTTP 422)."""

    def __init__(self, campo: str, mensagem: str) -> None:
        super().__init__(f"{campo}: {mensagem}")
        self.campo = campo
        self.mensagem = mensagem


class PacienteNaoEncontrado(ErroDominio):
    """HTTP 404."""

    def __init__(self, paciente_id: str) -> None:
        super().__init__(f"paciente {paciente_id!r} não encontrado")
        self.paciente_id = paciente_id


class PacienteJaExiste(ErroDominio):
    """HTTP 409."""

    def __init__(self, paciente_id: str) -> None:
        super().__init__(f"paciente {paciente_id!r} já existe")
        self.paciente_id = paciente_id


class AlertaNaoEncontrado(ErroDominio):
    """HTTP 404."""

    def __init__(self, alerta_id: int) -> None:
        super().__init__(f"alerta {alerta_id} não encontrado")
        self.alerta_id = alerta_id
