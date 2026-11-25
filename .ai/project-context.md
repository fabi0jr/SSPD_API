# Project Context

Mapa técnico condensado, gerado em 25/11/2026 sobre a branch `docs/readme` (commit `a0ebf7f`). Descreve o que foi observado no repositório; o que só existe em plano está marcado como **planejado**.

## Overview

Monitoramento remoto de pacientes (trabalho acadêmico, dados fictícios): sensores enviam sinais vitais para uma API, e um worker gera alertas. Hoje rodam a API (`GET /health`, `POST /leituras`) e o PostgreSQL. Domínio, casos de uso e fila estão prontos, mas **nenhum processo de worker os executa**. Instruções e regras do projeto: [CLAUDE.md](../CLAUDE.md) e `.claude/rules/`.

## Repository Structure

| Caminho | Conteúdo |
|---|---|
| `backend/app/dominio/` | `entidades.py`, `regras.py`, `erros.py`, `portas.py` |
| `backend/app/aplicacao/` | `registrar_leitura.py`, `processar_pendentes.py` |
| `backend/app/adaptadores/entrada/http/` | FastAPI: `main.py`, `deps.py`, `schemas.py`, `seguranca.py`, `routers/` |
| `backend/app/adaptadores/saida/postgres/` | `db.py`, `models.py`, `mapeamento.py`, `repositorios.py`, `fila_pendentes.py` |
| `backend/app/` | `composicao.py`, `config.py`, `seed.py` |
| `backend/migrations/` | Alembic, revisão `0001` |
| `backend/tests/` | `unit/`, `integracao/`, `fakes.py` |
| `docs/backend.md` | documentação técnica do backend |

**Planejado, ainda ausente:** `simulador/`, `tests-carga/`, `dashboard/`, `infra/`, `.github/workflows/`, `docker-compose.yml`, Dockerfiles.

## Technology Stack

Python 3.12.3 no ambiente local. As dependências usam faixas em `backend/requirements*.txt`, sem arquivo de lock. Versões instaladas em `backend/.venv` nesta data:

FastAPI 0.165.0 (Starlette 1.10.0), Uvicorn 0.54.0, SQLAlchemy 2.1.9, psycopg 3.3.9, Alembic 1.20.1, Pydantic 2.14.4, bcrypt 5.0.0, PyJWT 2.15.1 (declarado, ainda não importado), pytest 8.4.2, httpx2 2.16.2. Banco: PostgreSQL 16.

## Architecture

Hexagonal leve com injeção manual. Regra de dependência e portas: [.claude/rules/arquitetura.md](../.claude/rules/arquitetura.md) e [docs/backend.md](../docs/backend.md#2-arquitetura).

Pontos de entrada existentes (dentro de `backend/`):

| Função | Comando |
|---|---|
| API | `uvicorn app.composicao:criar_app_http --factory` |
| Migrations | `alembic upgrade head` |
| Seed | `python -m app.seed` |

Sem broker: a tabela `leituras` (`processada = false`) é a fila, consumida com `FOR UPDATE SKIP LOCKED`.

## Database

Tabelas `pacientes`, `leituras`, `alertas` e `usuarios`. `models.py` e `migrations/versions/` precisam andar juntos. O índice único parcial `uq_alertas_aberto_por_tipo` sustenta o anti-spam. Detalhes: [.claude/rules/banco.md](../.claude/rules/banco.md).

## APIs and Integrations

| Rota | Auth | Situação |
|---|---|---|
| `GET /health` | nenhuma | implementada |
| `POST /leituras` | `X-API-Key` | implementada, idempotente |
| `/docs`, `/redoc`, `/openapi.json` | nenhuma | expostas pelo FastAPI |
| login, pacientes, alertas | JWT | planejadas |

Contrato: [.claude/rules/api-http.md](../.claude/rules/api-http.md). Nenhuma integração com serviço externo observada no código.

## Authentication and Authorization

- Sensores: API key única (`SENSOR_API_KEY`), comparada com `hmac.compare_digest`.
- Usuários: o seed cria o administrador com senha em bcrypt. Login e JWT são **planejados**.
- Perfis ou RBAC: fora de escopo.

## Security Context

- **Dados sensíveis:** sinais vitais de pacientes (fictícios; categoria sensível pela LGPD), hash de senha em `usuarios`.
- **Segredos:** somente variáveis de ambiente; `.env` está no `.gitignore`; a aplicação não carrega `.env` sozinha.
- **Entrada não confiável:** corpo do `POST /leituras` (tipo e formato no Pydantic, faixas no domínio). Não há upload nem download de arquivos.
- **Controles presentes:** CORS por `CORS_ORIGINS`; respostas de erro sem stack trace; 503 para banco fora; a API não sobe sem `SENSOR_API_KEY`.
- **Logging:** uma linha por evento em stdout. Não há log de auditoria próprio.
- **Pendentes:** JWT, container não-root, HTTPS, firewall e rate limit (os três últimos são da infra).

Isto é um mapeamento, não uma auditoria.

## Development Conventions

Ver "Convenções" no [CLAUDE.md](../CLAUDE.md): português, commits pequenos, branch por funcionalidade, PR para a `dev`, push feito no VS Code. Não há configuração de lint, formatador ou análise estática no repositório.

## Testing

Comandos no [CLAUDE.md](../CLAUDE.md). Execução em 25/11/2026: **140 passaram** com `TEST_DATABASE_URL`; sem ela, 109 passaram e 31 foram pulados. Os testes de integração apagam as tabelas do banco apontado. Desempenho e falhas no ambiente completo ainda não foram executados.

## Critical Areas

- `fila_pendentes.py`: a transação única do lote é o que garante a recuperação do worker.
- `RepositorioLeiturasPg.salvar_se_nova`: idempotência do `POST /leituras`.
- Contrato do `POST /leituras`: o simulador e o dashboard dependem dele.
- `models.py` e migrations.
- `seguranca.py`, `config.py` e `composicao.py`: autenticação, segredos e montagem.
- `TEST_DATABASE_URL`: apontar para o banco errado apaga dados.

## Known Constraints

- Prazo curto; escopo mínimo demonstrável.
- `/dashboard`, `/infra` e `.github/workflows` pertencem a outros integrantes.
- Pelas referências locais (sem `fetch`), a `main` remota está em `e3af254`; o desenvolvimento está na `dev`.

## Documentation References

- [README.md](../README.md): estado atual e execução local
- [docs/backend.md](../docs/backend.md): arquitetura, domínio, banco, API, falhas, segurança e resultados
- [.env.example](../.env.example): variáveis de ambiente
- [.claude/rules/](../.claude/rules/): regras por área

## Divergences

| Onde | Divergência |
|---|---|
| `backend/pyproject.toml` | o marcador `integracao` cita `DATABASE_URL`; os testes usam `TEST_DATABASE_URL` |
| `adaptadores/entrada/http/main.py` | a descrição do Swagger cita `POST /auth/login`, que ainda não existe |
| `adaptadores/saida/postgres/db.py` | a docstring de `aguardar_banco` cita o worker (inexistente) e diz que o container sai após o timeout; a API captura o erro e sobe em modo degradado |
| 5 docstrings em `backend/` | citam "CLAUDE.md, seção N"; a tabela "Onde está cada detalhe" do CLAUDE.md faz a correspondência |

## Unknown / Needs Confirmation

- O modelo ABNT do professor não está no repositório.
- A versão inicial do simulador, citada como base, não está no repositório.
- Nada sobre a VPS ou o deploy é verificável por este repositório.
- Escolha entre k6 e Locust: a estrutura planejada cita k6.
