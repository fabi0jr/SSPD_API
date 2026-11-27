"""Caso de uso: cadastrar um usuário do dashboard (POST /auth/cadastro).

O hash de senha (bcrypt) é da borda HTTP: aqui chega só a função que gera o
hash, injetada pelo composicao.py (como o `verificar_senha` do Autenticar).
"""

from collections.abc import Callable

from app.dominio.entidades import Usuario
from app.dominio.portas import RepositorioUsuarios

# senha -> hash. A senha em claro nunca chega ao repositório.
GerarHashSenha = Callable[[str], str]


class CadastrarUsuario:
    def __init__(self, usuarios: RepositorioUsuarios, gerar_hash_senha: GerarHashSenha) -> None:
        self._usuarios = usuarios
        self._gerar_hash_senha = gerar_hash_senha

    def executar(self, email: str, senha: str) -> Usuario:
        """Devolve o usuário gravado (com id). Levanta UsuarioJaExiste se o e-mail já existir.

        O formato do e-mail e o tamanho da senha já foram validados na borda HTTP.
        """
        return self._usuarios.criar(email, self._gerar_hash_senha(senha))
