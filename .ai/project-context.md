# Project Context

Mapa técnico condensado, gerado em 25/11/2026 sobre a branch `docs/readme` (commit `a0ebf7f`). Descreve o que foi observado no repositório; o que só existe em plano está marcado como **planejado**.

## Overview

Monitoramento remoto de pacientes (trabalho acadêmico, dados fictícios): sensores enviam sinais vitais para uma API, e um worker gera alertas. Hoje rodam a API (`/health`, `POST /leituras`, login JWT e rotas do dashboard), o worker de alertas (loop de polling sobre a fila) e o PostgreSQL, ainda sem containers. Instruções e regras do projeto: [CLAUDE.md](../CLAUDE.md) e `.claude/rules/`.

## Repository Structure

| Caminho | Conteúdo |
|---|---|
| `backend/app/dominio/` | `entidades.py`, `regras.py`, `erros.py`, `portas.py` |
| `backend/app/aplicacao/` | `registrar_leitura.py`, `processar_pendentes.py`, `autenticar.py`, `cadastrar_paciente.py`, `consultas.py`, `reconhecer_alerta.py` |
| `backend/app/adaptadores/entrada/http/` | FastAPI: `main.py`, `deps.py`, `schemas.py`, `seguranca.py`, `routers/` |
| `backend/app/adaptadores/entrada/worker/` | `main.py`: loop do worker, logs de ciclo e SIGTERM |
| `backend/app/adaptadores/saida/postgres/` | `db.py`, `models.py`, `mapeamento.py`, `repositorios.py`, `fila_pendentes.py` |
| `backend/app/` | `composicao.py`, `config.py`, `seed.py` |
| `backend/migrations/` | Alembic, revisão `0001` |
| `backend/tests/` | `unit/`, `integracao/`, `fakes.py` |
| `docs/backend.md` | documentação técnica do backend |

| `backend/Dockerfile`, `backend/uvicorn-log.json` | imagem única de api, worker e migrate; formato dos logs do uvicorn |
| `simulador/` | `simulador.py` (só stdlib), `tests/`, `Dockerfile` |
| raiz | `docker-compose.yml` (postgres, migrate, api ×2, worker, simulador, nginx) |
| `infra/nginx/` | `default.conf`: Nginx mínimo, balanceamento entre as réplicas |
| `tests-carga/` | `leituras.js` (k6, roda com `grafana/k6:2.3.0` no Docker), `README.md` |

**Planejado, ainda ausente:** `dashboard/` (por enquanto em outro repositório), o resto de `infra/` (HTTPS, rate limit) e `.github/workflows/`.

## Technology Stack

Python 3.12.3 no ambiente local. As dependências ficam com versão fixa (`==`, diretas e indiretas) em `backend/requirements*.txt`, que servem de lock; o `requirements.txt` explica como atualizar. Versões fixadas:

FastAPI 0.165.0 (Starlette 1.10.0), Uvicorn 0.54.0, SQLAlchemy 2.1.9, psycopg 3.3.9, Alembic 1.20.1, Pydantic 2.14.4, bcrypt 5.0.0, PyJWT 2.15.1, pytest 8.4.2, httpx2 2.16.2. Banco: PostgreSQL 16.

## Architecture

Hexagonal leve com injeção manual. Regra de dependência e portas: [.claude/rules/arquitetura.md](../.claude/rules/arquitetura.md) e [docs/backend.md](../docs/backend.md#2-arquitetura).

Pontos de entrada existentes (dentro de `backend/`):

| Função | Comando |
|---|---|
| API | `uvicorn app.composicao:criar_app_http --factory` |
| Worker | `python -m app.composicao worker` |
| Migrations | `alembic upgrade head` |
| Seed | `python -m app.seed` |
| Simulador | `python simulador/simulador.py` (na raiz) |
| Sistema completo | `docker compose up -d --build --wait` (na raiz, com `.env`) |
| Carga (k6) | `docker run ... grafana/k6:2.3.0 run leituras.js` (em `tests-carga/`, ver o README da pasta) |

Sem broker: a tabela `leituras` (`processada = false`) é a fila, consumida com `FOR UPDATE SKIP LOCKED`.

## Database

Tabelas `pacientes`, `leituras`, `alertas` e `usuarios`. `models.py` e `migrations/versions/` precisam andar juntos. O índice único parcial `uq_alertas_aberto_por_tipo` sustenta o anti-spam. Detalhes: [.claude/rules/banco.md](../.claude/rules/banco.md).

## APIs and Integrations

| Rota | Auth | Situação |
|---|---|---|
| `GET /health` | nenhuma | implementada |
| `POST /leituras` | `X-API-Key` | implementada, idempotente |
| `/docs`, `/redoc`, `/openapi.json` | nenhuma | expostas pelo FastAPI |
| `POST /auth/login` | nenhuma | implementada |
| `/pacientes`, `/pacientes/{id}/ultima`, `/pacientes/{id}/leituras`, `/alertas`, `PATCH /alertas/{id}` | Bearer JWT | implementadas |

Contrato: [.claude/rules/api-http.md](../.claude/rules/api-http.md). Nenhuma integração com serviço externo observada no código.

## Authentication and Authorization

- Sensores: API key única (`SENSOR_API_KEY`), comparada com `hmac.compare_digest`.
- Usuários: o seed cria o administrador com senha em bcrypt. `POST /auth/login` emite JWT HS256 (`JWT_SECRET` ≥ 32 caracteres, `JWT_EXP_MINUTES`); as rotas do dashboard exigem `Authorization: Bearer`.
- Perfis ou RBAC: fora de escopo.

## Security Context

- **Dados sensíveis:** sinais vitais de pacientes (fictícios; categoria sensível pela LGPD), hash de senha em `usuarios`.
- **Segredos:** somente variáveis de ambiente; `.env` está no `.gitignore`; a aplicação não carrega `.env` sozinha.
- **Entrada não confiável:** corpo do `POST /leituras` (tipo e formato no Pydantic, faixas no domínio). Não há upload nem download de arquivos.
- **Controles presentes:** CORS por `CORS_ORIGINS`; respostas de erro sem stack trace; 503 para banco fora; a API não sobe sem `SENSOR_API_KEY`.
- **Logging:** uma linha por evento em stdout. Não há log de auditoria próprio.
- **Containers:** usuário não-root (`uid 10001`) nas duas imagens; `.dockerignore` deixa `.env`, `.venv` e testes de fora.
- **Pendentes (infra):** HTTPS, firewall e rate limit.

Isto é um mapeamento, não uma auditoria.

## Development Conventions

Ver "Convenções" no [CLAUDE.md](../CLAUDE.md): português, commits pequenos, branch por funcionalidade, PR para a `dev`, push feito no VS Code. Não há configuração de lint, formatador ou análise estática no repositório.

## Testing

Comandos no [CLAUDE.md](../CLAUDE.md). Execução em 25/11/2026: **270 passaram** com `TEST_DATABASE_URL`; sem ela, 228 passaram e 42 foram pulados. Os testes de integração apagam as tabelas do banco apontado. Desempenho e falhas no ambiente completo ainda não foram executados.

## Critical Areas

- `fila_pendentes.py`: a transação única do lote é o que garante a recuperação do worker.
- `worker/main.py`: o handler de SIGTERM só marca o evento, para o ciclo em andamento chegar ao commit.
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
- [docs/integracao-dashboard.md](../docs/integracao-dashboard.md) e [docs/integracao-infra.md](../docs/integracao-infra.md): protocolos com as outras partes da equipe
- [.env.example](../.env.example): variáveis de ambiente
- [.claude/rules/](../.claude/rules/): regras por área

## Divergences

| Onde | Divergência |
|---|---|
| `backend/pyproject.toml` | o marcador `integracao` cita `DATABASE_URL`; os testes usam `TEST_DATABASE_URL` |
| 5 docstrings em `backend/` | citam "CLAUDE.md, seção N"; a tabela "Onde está cada detalhe" do CLAUDE.md faz a correspondência |

## Unknown / Needs Confirmation

- O modelo ABNT do professor não está no repositório.
- Nada sobre a VPS ou o deploy é verificável por este repositório.
