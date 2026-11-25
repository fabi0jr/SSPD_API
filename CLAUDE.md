# Sistema Distribuído de Monitoramento de Pacientes

<!-- Manter este arquivo abaixo de 200 linhas. Detalhe de uma área vai para .claude/rules/ (com `paths`). Não usar imports com @: eles carregam no início e não economizam contexto. -->

Trabalho em equipe da disciplina de computação em nuvem / sistemas distribuídos (Fametro).
Tema: **monitoramento de pacientes, saúde digital e tolerância a falhas.**
Responsável pela parte coberta aqui: **Gerson** (backend: API, worker de alertas, simulador e testes funcionais).

**Prazo curto:** cortar escopo sempre que possível. Funcionar e ser demonstrável vale mais do que ser sofisticado.

## Requisitos do professor (obrigatórios)

- Problema real com relevância justificada (monitoramento remoto de pacientes crônicos e necessidade de alta disponibilidade em saúde)
- Arquitetura de sistema distribuído projetada, em uma VPS Linux configurada
- **Pelo menos dois serviços independentes que se comuniquem por rede**
- Persistência de dados e mecanismos de **segurança** e de **tolerância a falhas**
- Testes **funcionais**, de **desempenho** e de **falhas**, com procedimentos e resultados documentados (relatório ABNT no template do professor)
- Código-fonte em repositório GitHub

Entrega no Google Classroom: relatório em PDF, slides, link do GitHub, evidências técnicas (dentro do relatório) e identificação dos integrantes.

## Divisão da equipe

| Pessoa | Responsabilidade | Pastas |
|---|---|---|
| Gerson | API, worker de alertas, simulador, testes funcionais, script de carga, seções do relatório sobre o backend | `/backend`, `/simulador`, `/tests-carga` |
| Amigo 1 | VPS, Docker, Nginx, HTTPS, UFW, SSH, GitHub Actions, deploy, backups, execução dos testes de desempenho e de falha | `/infra`, `.github/workflows` |
| Amigo 2 | Dashboard, montagem do relatório ABNT e slides | `/dashboard`, `/docs` |

**Regra:** não alterar `/dashboard`, `/infra` nem `.github/workflows` sem pedido explícito. Se uma mudança no backend exigir algo nessas pastas (porta, variável de ambiente, healthcheck), avisar e descrever a mudança necessária. O `docker-compose.yml` da raiz é mantido pelo Amigo 1.

## Arquitetura

```
[Simulador de sensores] --HTTP POST + API key--> [Nginx] --> [API réplica 1]
                                                         \-> [API réplica 2]
                                                                  |
                                                            [PostgreSQL] <-- [Worker de alertas] (polling)
                                                                  ^
[Dashboard] --HTTP + JWT--> [Nginx] --> [API] ----------------------
```

Serviços (Docker Compose): `simulador`, `api` (stateless, 2 réplicas), `worker`, `postgres` (volume nomeado), `migrate` (one-shot, antes da API), `dashboard` e `nginx`.

Decisão: **sem broker de mensagens**. A fila é a tabela `leituras` (`processada = false`): se o worker cair, as leituras se acumulam e são processadas quando ele volta.

## Stack

Python 3.12, FastAPI + Uvicorn, SQLAlchemy 2.x **síncrono** + psycopg 3, Alembic, Pydantic v2, PyJWT + `bcrypt` direto (sem passlib), PostgreSQL 16, pytest com `fastapi.testclient` sobre **`httpx2`** (não `httpx`), k6 ou Locust para carga. Imagem base `python:3.12-slim`. Simulador só com a biblioteca padrão. Desenvolvimento em WSL2 (Ubuntu 24.04, Docker Engine no Ubuntu, sem Docker Desktop), em `~/SSPD_API`.

## Regra de dependência (obrigatória)

Arquitetura hexagonal leve, com portas em `typing.Protocol`. Imports internos permitidos: `dominio` só a stdlib; `aplicacao` só `dominio`; `adaptadores/entrada` só `aplicacao` e `dominio`; `adaptadores/saida` só `dominio`. Só `composicao.py` liga as implementações concretas (o `seed.py`, entrypoint de infraestrutura, usa o Postgres direto). **Se for preciso quebrar essa regra, parar e perguntar antes.** O teste `tests/unit/test_arquitetura.py` a verifica.

## Comandos

Dentro de `backend/`:

```bash
# venv (se sumir, recriar)
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# Postgres descartável (dados em memória), uma vez
docker run -d --name sspd-pg-teste --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=monitor -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=monitor_teste \
  -p 127.0.0.1:55432:5432 postgres:16-alpine

export TEST_DATABASE_URL=postgresql+psycopg://monitor:teste@localhost:55432/monitor_teste
.venv/bin/python -m pytest -q                       # tudo
.venv/bin/python -m pytest -q -m "not integracao"   # só unitários

# simulador (dentro de simulador/), sem banco
../backend/.venv/bin/python -m pytest -q
```

- `TEST_DATABASE_URL` aponta para um banco **descartável**: os testes apagam as tabelas. Nunca apontar para produção.
- Como subir a API localmente: [README.md](README.md), "Como rodar localmente".

## Convenções

- Código e comentários em português (nomes de domínio: `leitura`, `paciente`, `alerta`), exceto termos técnicos consagrados
- Logs em stdout, uma linha por evento, com timestamp
- Commits pequenos (um por unidade lógica), em português, com prefixos `feat:`, `fix:`, `test:`, `docs:`, `chore:`
- Branch por funcionalidade e PR para a **`dev`** (não para a `main`). A `main` recebe a `dev` quando ela estiver estável. O histórico mostra a contribuição de cada um
- Remote: `SSDP_API` → `https://github.com/fabi0jr/SSPD_API.git`. O terminal do Claude Code não tem credencial do GitHub: o push é feito por Gerson no VS Code
- **Nunca commitar `backend/.venv`** nem usar "adicionar tudo" sem conferir o `git status`: um `.venv` commitado faz o git apagar o venv local de quem troca de branch (no commit `e3af254` o domínio também foi apagado; restaurado no PR #2)
- Swagger em `/docs`. Manter os schemas com `examples`, porque o dashboard usa o Swagger como contrato
- Ao terminar uma etapa, atualizar [README.md](README.md) e [docs/backend.md](docs/backend.md). Se a mudança afetar o front ou a infra (rota, campo, status, variável, comando, porta, healthcheck), atualizar também [docs/integracao-dashboard.md](docs/integracao-dashboard.md) ou [docs/integracao-infra.md](docs/integracao-infra.md) no mesmo PR

## Segurança (parte do backend)

- API key para os sensores e JWT para os usuários, com senha em bcrypt
- Validação de entrada com Pydantic (tipo e formato) e no domínio (faixas)
- CORS restrito a `CORS_ORIGINS`; nada de stack trace na resposta em produção
- Segredos apenas em variáveis de ambiente. Nunca commitar `.env`; atualizar o `.env.example` quando surgir variável nova
- Dados de pacientes **fictícios** (LGPD, Lei 13.709/2018, art. 5º, II, e art. 11: dados de saúde são dados pessoais sensíveis)
- Container rodando com usuário não-root no Dockerfile
- UFW, SSH, HTTPS, fail2ban e rate limit no Nginx ficam com o Amigo 1

## Etapas (ordem de implementação)

| Etapa | Escopo | Situação |
|---|---|---|
| 1 | `dominio/`: entidades, validação, erros, regras e portas | ✅ na `dev` (commit `42ccb14`) |
| 2 | Fakes + `RegistrarLeitura` e `ProcessarPendentes` | ✅ na `dev` (PR #1) |
| 3 | Adaptador Postgres, migration, fila, seed e testes de integração | ✅ na `dev` (PR #3) |
| 4 | `composicao.py` + HTTP: `/health` e `POST /leituras` com API key | ✅ na `dev` (PR #5) |
| 5 | Login + JWT, rotas GET de pacientes, leituras e alertas, `PATCH` de alertas (consultas e `ReconhecerAlerta`) | ✅ na `dev` (PR #7) |
| 6 | Adaptador de entrada do worker (loop, logs, SIGTERM) | ✅ na `dev` (PR #8) |
| 7 | Dockerfile do backend (uma imagem para api, worker e migrate) e do simulador; ajustes no simulador | ✅ branch `feat/empacotamento` (aguardando PR) |
| 8 | Script k6 | a fazer |
| 9 | Textos do relatório em `docs/backend.md`: arquitetura (diagrama e regra de dependência), modelagem, regras de alerta, tolerância a falhas e segurança | parcial (cobre 1 a 7) |

## Fora de escopo (não fazer, a menos que sobre tempo)

RabbitMQ, Redis ou Kafka; notificação por e-mail, SMS ou Telegram; WebSocket/SSE (opcional no fim: `/alertas/stream`); multi-tenant, perfis ou RBAC; score clínico (NEWS2 etc.); ORM assíncrono.

## Onde está cada detalhe

Cada regra em `.claude/rules/` carrega quando um arquivo coberto por ela é lido ou editado. **Antes de planejar uma etapa ou criar o primeiro arquivo de uma área (worker, simulador, Dockerfile, k6), leia a regra correspondente.** Comentários no código que citam "CLAUDE.md, seção N" usam a numeração da primeira coluna.

| Seção | Assunto | Arquivo |
|---|---|---|
| 5 e 5.1 | Estrutura, portas, casos de uso, onde mora cada coisa | `.claude/rules/arquitetura.md` |
| 6 | Variáveis de ambiente | `.claude/rules/configuracao.md` |
| 7 | Modelo de dados e índices | `.claude/rules/banco.md` |
| 8 | Contrato da API | `.claude/rules/api-http.md` |
| 9 | Worker e regras de alerta | `.claude/rules/worker.md` |
| 10 | Tolerância a falhas e cenários de falha | `.claude/rules/tolerancia-falhas.md` |
| 11 e 14 | Segurança e convenções | este arquivo |
| 12 | Simulador | `.claude/rules/simulador.md` |
| 13 | Testes funcionais e de carga | `.claude/rules/testes.md` |
| 15 | Etapas (neste arquivo) e comandos dos serviços | `.claude/rules/empacotamento.md` |

Visão técnica condensada do repositório, divergências e pontos em aberto: `.ai/project-context.md`.
