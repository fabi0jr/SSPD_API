"""Caso de uso: conferir e-mail e senha no login (POST /auth/login).

O hash de senha (bcrypt) e o token (JWT) são da borda HTTP: aqui chega só a
função que confere a senha, injetada pelo composicao.py.
"""

from collections.abc import Callable

from app.dominio.entidades import Usuario
from app.dominio.erros import CredenciaisInvalidas
from app.dominio.portas import RepositorioUsuarios

# (senha, hash) -> bool. Recebe None quando o e-mail não existe: a implementação
# gasta o mesmo tempo mesmo assim, para o tempo de resposta não revelar quais
# e-mails estão cadastrados.
VerificarSenha = Callable[[str, str | None], bool]


class Autenticar:
    def __init__(self, usuarios: RepositorioUsuarios, verificar_senha: VerificarSenha) -> None:
        self._usuarios = usuarios
        self._verificar_senha = verificar_senha

    def executar(self, email: str, senha: str) -> Usuario:
        """Devolve o usuário ou levanta CredenciaisInvalidas (sem dizer o que errou)."""
        usuario = self._usuarios.por_email(email)
        senha_confere = self._verificar_senha(senha, usuario.senha_hash if usuario else None)
        if usuario is None or not senha_confere:
            raise CredenciaisInvalidas()
        return usuario
