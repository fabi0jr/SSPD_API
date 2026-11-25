"""Garante a regra de dependência da arquitetura hexagonal (CLAUDE.md, seção 5.1)."""

import ast
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

# Camada -> prefixos de módulos internos que ela pode importar (além da stdlib).
PERMITIDOS = {
    "dominio": ("app.dominio",),
    "aplicacao": ("app.dominio", "app.aplicacao"),
}


def _imports(arquivo: Path) -> list[str]:
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    nomes: list[str] = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.extend(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom):
            if no.level > 0:
                continue  # import relativo fica dentro do próprio pacote
            nomes.append(no.module or "")
    return nomes


def _permitido(modulo: str, prefixos: tuple[str, ...]) -> bool:
    raiz = modulo.split(".")[0]
    if raiz in sys.stdlib_module_names:
        return True
    return any(modulo == p or modulo.startswith(p + ".") for p in prefixos)


@pytest.mark.parametrize("camada", sorted(PERMITIDOS))
def test_camada_so_importa_o_permitido(camada):
    violacoes = [
        f"{arquivo.relative_to(APP)} importa {modulo}"
        for arquivo in sorted((APP / camada).rglob("*.py"))
        for modulo in _imports(arquivo)
        if not _permitido(modulo, PERMITIDOS[camada])
    ]
    assert violacoes == []
