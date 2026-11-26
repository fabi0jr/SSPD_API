"""Leitura das variáveis de ambiente (CLAUDE.md, seção 6).

Cada serviço lê só o que usa, na hora em que precisa: assim o worker não
exige JWT_SECRET, e a importação deste módulo nunca falha.
"""

import os


class ConfiguracaoAusente(RuntimeError):
    pass


def obrigatoria(nome: str) -> str:
    valor = os.environ.get(nome, "").strip()
    if not valor:
        raise ConfiguracaoAusente(f"variável de ambiente {nome} não definida")
    return valor


def inteiro(nome: str, padrao: int) -> int:
    valor = os.environ.get(nome, "").strip()
    if not valor:
        return padrao
    try:
        return int(valor)
    except ValueError:
        raise ConfiguracaoAusente(f"variável de ambiente {nome} precisa ser inteira (recebido {valor!r})")


def database_url() -> str:
    return obrigatoria("DATABASE_URL")


def lista(nome: str) -> list[str]:
    """Lista separada por vírgula (ex.: CORS_ORIGINS). Vazia se não definida."""
    return [item.strip() for item in os.environ.get(nome, "").split(",") if item.strip()]
