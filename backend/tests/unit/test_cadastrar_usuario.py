"""CadastrarUsuario (POST /auth/cadastro) com o repositório fake."""

import pytest

from app.aplicacao.cadastrar_usuario import CadastrarUsuario
from app.dominio.erros import UsuarioJaExiste
from tests.fakes import RepositorioUsuariosFake


def hash_falso(senha: str) -> str:
    """Hash determinístico, para o teste conferir que o caso de uso o aplicou."""
    return f"hash({senha})"


@pytest.fixture
def cadastrar(banco) -> CadastrarUsuario:
    return CadastrarUsuario(RepositorioUsuariosFake(banco), hash_falso)


def test_cria_usuario_com_id_e_grava_o_hash(cadastrar, banco):
    usuario = cadastrar.executar("fulano@exemplo.com", "senha-segura-123")

    assert (usuario.id, usuario.email, usuario.senha_hash) == (1, "fulano@exemplo.com", "hash(senha-segura-123)")
    assert banco.usuarios["fulano@exemplo.com"] == usuario


def test_senha_em_claro_nao_chega_ao_repositorio(cadastrar, banco):
    cadastrar.executar("fulano@exemplo.com", "senha-segura-123")
    assert "senha-segura-123" != banco.usuarios["fulano@exemplo.com"].senha_hash


def test_ids_seguem_em_sequencia(cadastrar):
    assert cadastrar.executar("a@exemplo.com", "senha-segura-1").id == 1
    assert cadastrar.executar("b@exemplo.com", "senha-segura-2").id == 2


def test_email_repetido_levanta_usuario_ja_existe(cadastrar, banco):
    original = cadastrar.executar("fulano@exemplo.com", "senha-segura-123")

    with pytest.raises(UsuarioJaExiste) as erro:
        cadastrar.executar("fulano@exemplo.com", "outra-senha-456")

    assert str(erro.value) == "e-mail já cadastrado"
    assert banco.usuarios["fulano@exemplo.com"] == original  # a senha não foi trocada
