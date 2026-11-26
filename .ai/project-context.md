# Project Context

Mapa técnico condensado, atualizado em 26/11/2026 sobre a branch `feat/compose` (commit `74c0330`, 7 commits à frente da `dev`, aguardando PR). Descreve o que foi observado no repositório; o que só existe em plano está marcado como **planejado**.

## Overview

Monitoramento remoto de pacientes (trabalho acadêmico, dados fictícios): sensores enviam sinais vitais para uma API, e um worker gera alertas. O backend está completo e sobe inteiro com `docker compose`: PostgreSQL, `migrate` (one-shot), API em 2 réplicas atrás do Nginx, worker de alertas e simulador de sensores. Faltam o dashboard (de outro integrante), a infra da VPS (HTTPS, rate limit, firewall, CI) e os textos do relatório. Instruções e regras do projeto: [CLAUDE.md](../CLAUDE.md) e `.claude/rules/`.

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
| `backend/Dockerfile`, `backend/uvicorn-log.json` | imagem única de api, worker e migrate; formato dos logs do uvicorn |
| `simulador/` | `simulador.py` (só stdlib), `tests/`, `Dockerfile` |
| `tests-carga/` | `leituras.js` (k6, roda com `grafana/k6:2.3.0` no Docker), `README.md` |
| raiz | `docker-compose.yml`, `.env.example` |
| `infra/nginx/` | `default.conf`: Nginx mínimo, balanceamento entre as réplicas |
| `docs/` | `backend.md`, `integracao-dashboard.md`, `integracao-infra.md` |

**Planejado, ainda ausente nesta branch:** o resto de `infra/` (HTTPS, rate limit) e `.github/workflows/`. Sobre o dashboard, ver "Divergences".

## Technology Stack

Python 3.12.3 no ambiente local; imagens em `python:3.12-slim`. As dependências ficam com versão fixa (`==`, diretas e indiretas) em `backend/requirements*.txt`, que servem de lock; o `requirements.txt` explica como atualizar. Versões fixadas:

FastAPI 0.165.0 (Starlette 1.10.0), Uvicorn 0.54.0, SQLAlchemy 2.1.9, psycopg 3.3.9, Alembic 1.20.1, Pydantic 2.14.4, bcrypt 5.0.0, PyJWT 2.15.1, pytest 8.4.2, httpx2 2.16.2. No Compose: `postgres:16-alpine` e `nginx:1.28-alpine`.

## Architecture

Hexagonal leve com injeção manual. Regra de dependência e portas: [.claude/rules/arquitetura.md](../.claude/rules/arquitetura.md) e [docs/backend.md](../docs/backend.md#2-arquitetura).

Pontos de entrada:

| Função | Comando |
|---|---|
| API | `uvicorn app.composicao:criar_app_http --factory` (em `backend/`; o CMD da imagem acrescenta `--proxy-headers`, `--forwarded-allow-ips=*` e `--log-config`) |
| Worker | `python -m app.composicao worker` |
| Migrations | `alembic upgrade head` |
| Seed | `python -m app.seed` |
| Simulador | `python simulador/simulador.py` (na raiz) |
| Sistema completo | `docker compose up -d --build --wait` (na raiz, com `.env`) |
| Carga (k6) | `docker run ... grafana/k6:2.3.0 run leituras.js` (em `tests-carga/`, ver o README da pasta) |

Sem broker: a tabela `leituras` (`processada = false`) é a fila, consumida com `FOR UPDATE SKIP LOCKED`.

## Database

Tabelas `pacientes`, `leituras`, `alertas` e `usuarios`. `models.py` e `migrations/versions/` precisam andar juntos. O índice único parcial `uq_alertas_aberto_por_tipo` sustenta o anti-spam. Só o serviço `migrate` aplica migrations (as réplicas não). Detalhes: [.claude/rules/banco.md](../.claude/rules/banco.md).

## APIs and Integrations

| Rota | Auth | Situação |
|---|---|---|
| `GET /health` | nenhuma | implementada; 503 com banco fora |
| `POST /leituras` | `X-API-Key` | implementada, idempotente |
| `POST /auth/login` | nenhuma | implementada |
| `GET /pacientes`, `POST /pacientes`, `GET /pacientes/{id}/ultima`, `GET /pacientes/{id}/leituras`, `GET /alertas`, `PATCH /alertas/{id}` | Bearer JWT | implementadas |
| `/docs`, `/redoc`, `/openapi.json` | nenhuma | expostas pelo FastAPI |

As rotas ficam na raiz (sem prefixo `/api`); listagens usam `limit`. Contrato: [.claude/rules/api-http.md](../.claude/rules/api-http.md) e, para o front, [docs/integracao-dashboard.md](../docs/integracao-dashboard.md). Nenhuma integração com serviço externo no código.

## Authentication and Authorization

- Sensores: API key única (`SENSOR_API_KEY`), comparada com `hmac.compare_digest`.
- Usuários: o seed cria o administrador com senha em bcrypt. `POST /auth/login` emite JWT HS256 (`JWT_SECRET` ≥ 32 caracteres, `JWT_EXP_MINUTES`); as rotas do dashboard exigem `Authorization: Bearer`.
- Perfis ou RBAC: fora de escopo.

## Security Context

- **Dados sensíveis:** sinais vitais de pacientes (fictícios; categoria sensível pela LGPD), hash de senha em `usuarios`.
- **Segredos:** somente variáveis de ambiente; `.env` está no `.gitignore`; a aplicação não carrega `.env` sozinha. No Compose, cada serviço recebe só as variáveis de que precisa, e as obrigatórias usam `${VAR:?}`. A `DATABASE_URL` é montada a partir de `POSTGRES_*`, por isso a senha deve ter só letras e números.
- **Superfície exposta (Compose):** só o Nginx publica porta (`HTTP_PORT`, padrão 80). Postgres e réplicas da API não publicam portas; a API confia em `X-Forwarded-For` (`--forwarded-allow-ips=*`), então publicar a 8000 seria um problema.
- **Entrada não confiável:** corpo do `POST /leituras` e do login (Pydantic + faixas no domínio). Nginx limita o corpo a 64 KB. Não há upload nem download de arquivos.
- **Controles presentes:** CORS por `CORS_ORIGINS`; respostas de erro sem stack trace; 503 para banco fora; a API não sobe sem `SENSOR_API_KEY` nem com `JWT_SECRET` curto; `server_tokens off` no Nginx.
- **Containers:** usuário não-root (`uid 10001`) nas duas imagens; `.dockerignore` deixa `.env`, `.venv` e testes de fora; logs com rotação (3 × 10 MB).
- **Logging:** uma linha por evento em stdout. Não há log de auditoria próprio.
- **Pendentes (infra):** HTTPS, firewall, SSH, fail2ban e rate limit.

Isto é um mapeamento, não uma auditoria.

## Development Conventions

Ver "Convenções" no [CLAUDE.md](../CLAUDE.md): português, commits pequenos, branch por funcionalidade, PR para a `dev`, push feito no VS Code. Não há configuração de lint, formatador ou análise estática no repositório.

## Testing

Comandos no [CLAUDE.md](../CLAUDE.md). Execução em 26/11/2026 nesta branch: backend **270 passaram** com `TEST_DATABASE_URL` (228 unitários + 42 de integração; sem a variável, os 42 são pulados); simulador **35 passaram**. Os testes de integração apagam as tabelas do banco apontado.

Desempenho: ensaio local registrado em [docs/backend.md](../docs/backend.md#104-desempenho) (1 processo satura em ~165 req/s; com 2 réplicas no Compose, 500 sensores deram 238 req/s e p95 de 33 ms). Falhas: os 4 cenários foram ensaiados localmente com o Compose (seção 8.2 do mesmo arquivo). As execuções oficiais na VPS ficam com a infra.

## Infrastructure and Deployment

- Ordem de subida no Compose: `postgres` healthy → `migrate` concluído → `api` e `worker` → `nginx` (após as réplicas healthy) → `simulador`.
- Healthcheck da API no Compose (Python `urllib`, a imagem não tem curl); a imagem não declara `HEALTHCHECK` porque o worker usa a mesma imagem.
- O Nginx resolve o nome `api` ao iniciar: após recriar as réplicas, reiniciar o nginx. A retentativa na outra réplica vale só para rotas idempotentes e não inclui `http_503`.
- O `docker-compose.yml` e o `infra/nginx/default.conf` foram feitos pelo Gerson a pedido do Amigo 1 e passam a ser mantidos por ele.
- Protocolo com a infra (deploy, variáveis, smoke test, CI, k6): [docs/integracao-infra.md](../docs/integracao-infra.md). Nada sobre a VPS é verificável por este repositório.

## Critical Areas

- `fila_pendentes.py`: a transação única do lote é o que garante a recuperação do worker.
- `worker/main.py`: o handler de SIGTERM só marca o evento, para o ciclo em andamento chegar ao commit. No Compose, o worker usa a forma exec para receber o sinal.
- `RepositorioLeiturasPg.salvar_se_nova`: idempotência do `POST /leituras`, da qual depende a retentativa do Nginx e o reenvio do simulador.
- Contrato HTTP (`schemas.py`, routers): o simulador, o k6 e o dashboard dependem dele.
- `models.py` e migrations.
- `seguranca.py`, `config.py` e `composicao.py`: autenticação, segredos e montagem.
- `docker-compose.yml` e `infra/nginx/default.conf`: portas publicadas e repasse de headers.
- `TEST_DATABASE_URL`: apontar para o banco errado apaga dados.

## Known Constraints

- Prazo curto; escopo mínimo demonstrável.
- `/dashboard`, `/infra` e `.github/workflows` pertencem a outros integrantes.
- Refs remotas (sem `fetch` nesta análise): `SSDP_API/dev` = `a107e90` (PR #11); `SSDP_API/main` = `6c6f357`; a `main` local está em `e3af254`, atrás da remota.

## Documentation References

- [README.md](../README.md): estado atual, Compose e execução local
- [docs/backend.md](../docs/backend.md): arquitetura, domínio, banco, API, falhas, segurança, testes e resultados
- [docs/integracao-dashboard.md](../docs/integracao-dashboard.md) e [docs/integracao-infra.md](../docs/integracao-infra.md): protocolos com as outras partes da equipe
- [tests-carga/README.md](../tests-carga/README.md): como rodar o k6
- [.env.example](../.env.example): variáveis de ambiente
- [.claude/rules/](../.claude/rules/): regras por área

## Divergences

| Onde | Divergência |
|---|---|
| `SSDP_API/main` (`6c6f357`, 26/11 01:23, outro integrante) | adiciona `dashboard/index.html` **direto na `main`**, fora da `dev`. O commit é anterior ao ajuste dos docs para "dashboard em outro repositório" (`74c0330`, 26/11 09:29), mas continua na `main` e contradiz essa decisão (CLAUDE.md, README, `integracao-dashboard.md`) e o fluxo de PR para a `dev` |
| `dashboard/index.html` × API | o front segue outro contrato: chama `/api/*` (o Nginx atual não tem esse prefixo), envia `X-API-Key` em vez de JWT, usa `/resumo` (inexistente), `?limite=` (a API usa `limit`) e campos que a API não devolve (`frequencia_cardiaca`, `temperatura`, `ultima_medicao`, `paciente_nome`, `mensagem`). A API usa `fc`, `temp`, `ultima_leitura` aninhada e `severidade`. Sem ajuste de um dos lados, o dashboard não funciona contra este backend (exceto no modo `?demo`) |
| `dashboard/index.html` × `regras.py` | o front tem faixas próprias para colorir os cartões; não foi conferido se batem com as regras de alerta do domínio |
| `backend/pyproject.toml` | o marcador `integracao` cita `DATABASE_URL`; os testes usam `TEST_DATABASE_URL` |
| 5 docstrings em `backend/` | citam "CLAUDE.md, seção N"; a tabela "Onde está cada detalhe" do CLAUDE.md faz a correspondência |

## Unknown / Needs Confirmation

- Se a equipe voltou atrás e o dashboard fica em `/dashboard` neste repositório (o commit na `main` sugere isso), e quem ajusta o contrato: o front ou a API.
- Como o front será servido na VPS (mesmo Nginx ou outro domínio), o que decide o `CORS_ORIGINS` e se o Nginx precisa de um `location /api/`.
- O modelo ABNT do professor não está no repositório.
- Estado da VPS e do deploy.
