# Sistema Distribuído de Monitoramento de Pacientes

Trabalho em equipe da disciplina de Computação em Nuvem / Sistemas Distribuídos (Fametro).
Tema: **monitoramento de pacientes, saúde digital e tolerância a falhas**.

Sensores enviam sinais vitais (frequência cardíaca, SpO₂ e temperatura) de pacientes crônicos acompanhados à distância. Nesse cenário o sistema precisa continuar recebendo dados mesmo quando uma parte dele falha.

> Todos os dados de pacientes são **fictícios**. Pela LGPD (Lei 13.709/2018, art. 5º, II, e art. 11), dados de saúde são dados pessoais sensíveis.

## Estado atual

Situação em 26/11/2026. O projeto está em desenvolvimento: hoje a API **recebe e grava leituras** e atende o dashboard (login, pacientes, histórico e alertas), o worker **gera os alertas** a partir das leituras pendentes e o simulador **envia leituras** de sensores fictícios. Tudo sobe com um `docker compose up`: PostgreSQL, migrations, duas réplicas da API atrás do Nginx, worker e simulador. Também há um teste de carga com k6.

| Parte | Situação |
|---|---|
| Domínio: entidades, validação e regras de alerta | ✅ pronto |
| Casos de uso `RegistrarLeitura` e `ProcessarPendentes` | ✅ prontos |
| Adaptador PostgreSQL: repositórios, fila de pendentes, migration e seed | ✅ pronto |
| API: `GET /health` e `POST /leituras` | ✅ pronto |
| Login (JWT), consultas de pacientes, leituras e alertas, reconhecimento de alertas | ✅ pronto |
| Worker de alertas (ciclo periódico, logs e encerramento por SIGTERM) | ✅ pronto |
| Simulador de sensores (buffer local e reenvio) | ✅ pronto |
| Dockerfile do backend e do simulador | ✅ pronto |
| Docker Compose (2 réplicas da API, Nginx mínimo) | ✅ pronto |
| Teste de carga (k6) | ✅ pronto (execução na VPS com a infraestrutura) |
| Dashboard (`dashboard/`, primeira versão em andamento) | 🟡 em andamento |
| HTTPS, rate limit, VPS e CI/CD | ⬜ a fazer |

## Arquitetura

```
[Simulador de sensores] --HTTP POST + API key--> [Nginx] --> [API réplica 1]
                                                         \-> [API réplica 2]
                                                                  |
                                                            [PostgreSQL] <-- [Worker de alertas] (polling)
                                                                  ^
[Dashboard] --HTTP + JWT--> [Nginx] --> [API] ----------------------
```

Tudo está neste repositório. O backend, o simulador e o Nginx sobem com o [docker-compose.yml](docker-compose.yml). O dashboard fica em `dashboard/` e conversa com a API só por HTTP, pelo contrato descrito em [docs/integracao-dashboard.md](docs/integracao-dashboard.md).

Não há broker de mensagens: a fila do worker é a própria tabela `leituras` (`processada = false`). Se o worker parar, as leituras se acumulam no banco e são processadas quando ele voltar.

O backend segue uma arquitetura hexagonal leve (portas e adaptadores). Os detalhes estão em [docs/backend.md](docs/backend.md).

## Tecnologias

Python 3.12, FastAPI, Uvicorn, SQLAlchemy 2, psycopg 3, Alembic, Pydantic 2, bcrypt, PyJWT, PostgreSQL 16, pytest, k6 e Docker (imagem `python:3.12-slim`).

## Como subir com Docker Compose

Pré-requisitos: Docker Engine com o plugin `docker compose`. Na raiz do repositório:

```bash
cp .env.example .env
# troque no .env: POSTGRES_PASSWORD, JWT_SECRET e SENSOR_API_KEY (openssl rand -hex 32),
# ADMIN_EMAIL e ADMIN_PASSWORD. Se a porta 80 estiver ocupada, mude HTTP_PORT.

docker compose up -d --build --wait
docker compose ps                  # migrate "Exited (0)", api-1 e api-2 "healthy"
curl http://localhost/health       # {"status":"ok","db":"ok"}
docker compose logs -f worker simulador
```

O Swagger fica em <http://localhost/docs>. `docker compose down` para tudo e mantém o banco; `docker compose down -v` apaga também o volume do banco. Deploy na VPS e detalhes de cada serviço: [docs/integracao-infra.md](docs/integracao-infra.md), seção 0.

## Como rodar localmente

Sem Docker Compose, cada processo no terminal (útil para desenvolver o backend).

Pré-requisitos: Linux ou WSL2, Git, Python 3.12 e Docker.

### 1. Baixar o código

```bash
git clone https://github.com/fabi0jr/SSPD_API.git
cd SSPD_API
git switch dev        # o desenvolvimento fica na dev
```

### 2. Subir o PostgreSQL

```bash
docker run -d --name sspd-pg-dev \
  -e POSTGRES_USER=monitor -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=monitor \
  -v sspd-pg-dev:/var/lib/postgresql/data \
  -p 127.0.0.1:55433:5432 postgres:16-alpine
```

### 3. Instalar e configurar

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

export DATABASE_URL=postgresql+psycopg://monitor:dev@localhost:55433/monitor
export SENSOR_API_KEY=chave-local
export JWT_SECRET=segredo-local-com-pelo-menos-32-caracteres
export CORS_ORIGINS=http://localhost:5173
export ADMIN_EMAIL=admin@example.com
export ADMIN_PASSWORD=admin-local
export SEED_PACIENTES=10
```

Os valores acima servem **só para uso local**. A aplicação lê variáveis de ambiente e não carrega o arquivo `.env` sozinha.

### 4. Criar as tabelas e iniciar a API

```bash
.venv/bin/alembic upgrade head     # cria as tabelas
.venv/bin/python -m app.seed       # usuário admin e pacientes P001 a P010
.venv/bin/uvicorn app.composicao:criar_app_http --factory --port 8000
```

Se a porta 8000 estiver ocupada, troque o valor de `--port`. O Swagger fica em <http://localhost:8000/docs>.

### 5. Testar

Em outro terminal:

```bash
curl http://localhost:8000/health

CORPO="{\"id\":\"$(cat /proc/sys/kernel/random/uuid)\",\"paciente_id\":\"P001\",\"fc\":82,\"spo2\":97.4,\"temp\":36.7,\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"

curl -i -X POST http://localhost:8000/leituras \
  -H "X-API-Key: chave-local" -H "Content-Type: application/json" -d "$CORPO"
```

A primeira chamada responde `201` com `"nova": true`. Repetir o mesmo `curl` responde `200` com `"nova": false`, sem gravar a leitura de novo.

Cadastro de um usuário do dashboard (rota pública, sem token):

```bash
curl -i -X POST http://localhost:8000/auth/cadastro -H "Content-Type: application/json" \
  -d '{"email":"fulano@exemplo.com","senha":"senha-segura-123"}'
```

Responde `201` com `{"id": 2, "email": "fulano@exemplo.com"}`, sem token. Repetir o mesmo `curl` responde `409` (`e-mail já cadastrado`). A senha precisa ter pelo menos 8 caracteres. A conta criada pode fazer login como o admin do seed.

Rotas do dashboard, com o token do login:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","senha":"admin-local"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/pacientes
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/pacientes/P001/leituras?limit=5"
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/alertas?status=aberto"
```

No Swagger, use o botão **Authorize** e cole só o `access_token`.

### 6. Iniciar o worker de alertas

Em outro terminal, dentro de `backend/` e com o mesmo `DATABASE_URL`:

```bash
export DATABASE_URL=postgresql+psycopg://monitor:dev@localhost:55433/monitor
.venv/bin/python -m app.composicao worker
```

Cada ciclo que processa alguma leitura gera uma linha como `ciclo: processadas=1 alertas=1 backlog=0 duracao_ms=8` (sem leituras novas, o worker não escreve nada). Envie uma leitura com `"spo2": 85` e o alerta `SPO2_BAIXA` aparece em `GET /alertas`.

### 7. Iniciar o simulador de sensores

Em outro terminal, na raiz do repositório. O simulador só usa a biblioteca padrão do Python, então não precisa de venv:

```bash
API_URL=http://localhost:8000/leituras SENSOR_API_KEY=chave-local \
N_PACIENTES=10 INTERVALO=2 python3 simulador/simulador.py
```

`N_PACIENTES` não pode passar de `SEED_PACIENTES`: os sensores usam os códigos `P001`, `P002`... criados pelo seed, e um paciente inexistente dá 404. A cada 10 s sai uma linha de resumo, como `resumo: enviadas=100 novas=100 duplicadas=0 rejeitadas=0 falhas=0 perdidas=0 buffer=0`. Com a API parada, as leituras ficam no buffer (`buffer=` cresce) e são reenviadas quando ela volta.

Para encerrar: `Ctrl+C` na API, no worker e no simulador e `docker stop sspd-pg-dev` no banco.

### Imagens Docker

Na raiz do repositório:

```bash
docker build -t sspd-backend ./backend       # uma imagem para api, worker e migrate
docker build -t sspd-simulador ./simulador
```

O comando padrão da imagem do backend sobe a API. O worker e o migrate usam a mesma imagem com outro comando. O [docker-compose.yml](docker-compose.yml) já usa as duas. Os comandos, as variáveis e o healthcheck de cada serviço estão no [protocolo com a infra](docs/integracao-infra.md#1-serviços-do-backend). As duas imagens rodam com um usuário sem privilégios (`uid 10001`).

## API disponível

| Método | Rota | Autenticação | Descrição |
|---|---|---|---|
| GET | `/health` | nenhuma | Verifica a API e o banco |
| POST | `/leituras` | header `X-API-Key` | Registra uma leitura (idempotente) |
| POST | `/auth/cadastro` | nenhuma | Cria um usuário do dashboard (e-mail e senha) |
| POST | `/auth/login` | nenhuma | Devolve um token JWT |
| GET | `/pacientes` | JWT | Pacientes com a última leitura e os alertas abertos |
| POST | `/pacientes` | JWT | Cadastra um paciente |
| GET | `/pacientes/{id}/ultima` | JWT | Última leitura (`null` se ainda não houver) |
| GET | `/pacientes/{id}/leituras` | JWT | Histórico, com `desde`, `ate` e `limit` |
| GET | `/alertas` | JWT | Alertas, com `status`, `paciente_id` e `limit` |
| PATCH | `/alertas/{id}` | JWT | Reconhece um alerta (idempotente) |

O contrato completo, com validações e códigos de erro, está em [docs/backend.md](docs/backend.md#7-api-http). Protocolos de integração: [dashboard](docs/integracao-dashboard.md) e [infra](docs/integracao-infra.md).

## Variáveis de ambiente

| Variável | Usada por | Obrigatória | Descrição |
|---|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Compose (`postgres`) | só a senha | Usuário, senha e banco do PostgreSQL. A senha só com letras e números (`openssl rand -hex 32`), porque o Compose a põe na `DATABASE_URL` sem codificação |
| `DATABASE_URL` | API, worker, migrations, seed | sim | Conexão com o PostgreSQL. No Compose, é montada a partir das `POSTGRES_*` (não declarar no `.env`). Fora dele, caracteres especiais na senha vão codificados (`@` → `%40`) |
| `SENSOR_API_KEY` | API, simulador | sim | Chave exigida dos sensores (o mesmo valor nos dois) |
| `JWT_SECRET` | API | sim | Segredo que assina os tokens; no mínimo 32 caracteres |
| `JWT_EXP_MINUTES` | API | não (padrão `60`) | Validade do token, em minutos |
| `CORS_ORIGINS` | API | não | Origens liberadas, separadas por vírgula |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | seed | sim | Usuário administrador criado pelo seed |
| `SEED_PACIENTES` | seed | não (padrão `50`) | Quantidade de pacientes fictícios |
| `WORKER_INTERVALO` | worker | não (padrão `2`) | Segundos entre um ciclo e outro (inteiro maior que zero) |
| `WORKER_LOTE` | worker | não (padrão `500`) | Máximo de leituras por ciclo (inteiro maior que zero) |
| `API_URL` | simulador | sim | URL completa do `POST /leituras`. No Compose, fixada em `http://nginx/leituras` |
| `N_PACIENTES` | simulador | não (padrão `10`) | Quantidade de sensores (um por paciente, a partir de `P001`) |
| `INTERVALO` | simulador | não (padrão `2`) | Segundos entre as leituras de cada sensor (aceita fração, ex. `0.5`) |
| `HTTP_PORT` | Compose (`nginx`) | não (padrão `80`) | Porta do Nginx publicada no host |
| `TEST_DATABASE_URL` | testes de integração | não | Banco **descartável**: os testes apagam as tabelas |

Todas estão no [.env.example](.env.example). Nunca commite o arquivo `.env`.

## Testes

Dentro de `backend/`:

```bash
# unitários (sem banco)
.venv/bin/python -m pytest -q -m "not integracao"

# todos, com um PostgreSQL descartável (criar o container uma vez)
docker run -d --name sspd-pg-teste --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=monitor -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=monitor_teste \
  -p 127.0.0.1:55432:5432 postgres:16-alpine

export TEST_DATABASE_URL=postgresql+psycopg://monitor:teste@localhost:55432/monitor_teste
.venv/bin/python -m pytest -q
```

Último resultado (27/11/2026): **296 testes passando**, sendo 249 unitários e 47 de integração. Sem `TEST_DATABASE_URL`, os de integração são pulados.

Testes do simulador (unitários, sem rede), dentro de `simulador/`. Qualquer ambiente com pytest serve, inclusive o venv do backend:

```bash
../backend/.venv/bin/python -m pytest -q
```

Último resultado (26/11/2026): **35 testes passando**.

> Nunca aponte `TEST_DATABASE_URL` para um banco com dados que você queira manter.

### Teste de carga (k6)

Com a API no ar (seção "Como rodar localmente"), dentro de `tests-carga/`:

```bash
mkdir -p resultados
docker run --rm --network host -u "$(id -u)" -v "$PWD":/carga -w /carga \
  -e API_KEY=chave-local -e SENSORES=10 -e N_PACIENTES=10 \
  grafana/k6:2.3.0 run -q leituras.js
```

Com o Docker Compose, acrescente `-e BASE_URL=http://localhost` (com a porta, se `HTTP_PORT` não for 80), use em `API_KEY` o `SENSOR_API_KEY` do `.env` e pare o simulador antes (`docker compose stop simulador`), para medir só a carga do k6.

Cada usuário virtual é um sensor que envia uma leitura a cada 2 s. O resumo (latência p50/p95/p99, requisições por segundo e taxa de erro) aparece no terminal e é salvo em `tests-carga/resultados/`. Cenários, variáveis e cuidados: [tests-carga/README.md](tests-carga/README.md).

## Estrutura do repositório

```
backend/
├── app/
│   ├── dominio/        # entidades, regras de alerta, erros e portas
│   ├── aplicacao/      # casos de uso
│   ├── adaptadores/
│   │   ├── entrada/    # HTTP (FastAPI) e worker (loop de polling)
│   │   └── saida/      # PostgreSQL
│   ├── composicao.py   # liga as implementações (entrypoint da API e do worker)
│   ├── config.py       # variáveis de ambiente
│   └── seed.py         # dados iniciais
├── migrations/         # Alembic
├── tests/              # unit/ e integracao/
├── Dockerfile          # imagem única: api, worker e migrate
└── uvicorn-log.json    # formato dos logs do uvicorn (com timestamp)
simulador/
├── simulador.py        # sensores fictícios, buffer local e reenvio
├── tests/
└── Dockerfile
infra/nginx/
└── default.conf        # Nginx mínimo: balanceamento entre as réplicas (HTTPS e rate limit: infra)
docker-compose.yml      # sistema completo: postgres, migrate, api ×2, worker, simulador e nginx
dashboard/              # front (consome a API pelo contrato de docs/integracao-dashboard.md)
tests-carga/
├── leituras.js         # teste de carga do POST /leituras (k6)
└── README.md
docs/backend.md         # documentação técnica do backend
docs/integracao-*.md    # protocolos com o dashboard e com a infra
```

## Equipe e contribuição

| Responsável | Área | Pastas |
|---|---|---|
| Gerson | API, worker, simulador, testes funcionais e script de carga | `backend/`, `simulador/`, `tests-carga/` |
| Integrante de infraestrutura | VPS, Docker, Nginx, segurança do servidor, CI/CD e deploy | `infra/`, `.github/workflows/` |
| Integrante de dashboard | Dashboard, relatório ABNT e slides | `dashboard/`, `docs/` |

Fluxo: uma branch por funcionalidade, commits pequenos em português (`feat:`, `fix:`, `test:`, `docs:`, `chore:`) e pull request para a `dev`. A `main` recebe a `dev` quando ela estiver estável.
