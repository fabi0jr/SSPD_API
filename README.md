# Sistema Distribuído de Monitoramento de Pacientes

Trabalho em equipe da disciplina de Computação em Nuvem / Sistemas Distribuídos (Fametro).
Tema: **monitoramento de pacientes, saúde digital e tolerância a falhas**.

Sensores enviam sinais vitais (frequência cardíaca, SpO₂ e temperatura) de pacientes crônicos acompanhados à distância. Nesse cenário o sistema precisa continuar recebendo dados mesmo quando uma parte dele falha.

> Todos os dados de pacientes são **fictícios**. Pela LGPD (Lei 13.709/2018, art. 5º, II, e art. 11), dados de saúde são dados pessoais sensíveis.

## Estado atual

Situação em 25/11/2026. O projeto está em desenvolvimento: hoje é possível **receber e gravar leituras** pela API. Os alertas ainda não são gerados em execução.

| Parte | Situação |
|---|---|
| Domínio: entidades, validação e regras de alerta | ✅ pronto |
| Casos de uso `RegistrarLeitura` e `ProcessarPendentes` | ✅ prontos |
| Adaptador PostgreSQL: repositórios, fila de pendentes, migration e seed | ✅ pronto |
| API: `GET /health` e `POST /leituras` | ✅ pronto |
| Login (JWT), consultas de pacientes, leituras e alertas | ⬜ a fazer |
| Processo do worker de alertas (o caso de uso já existe) | ⬜ a fazer |
| Simulador de sensores | ⬜ a fazer |
| Dockerfile e Docker Compose | ⬜ a fazer |
| Teste de carga (k6) | ⬜ a fazer |
| Dashboard, Nginx, HTTPS, VPS e CI/CD | ⬜ a fazer |

## Arquitetura planejada

```
[Simulador de sensores] --HTTP POST + API key--> [Nginx] --> [API réplica 1]
                                                         \-> [API réplica 2]
                                                                  |
                                                            [PostgreSQL] <-- [Worker de alertas] (polling)
                                                                  ^
[Dashboard] --HTTP + JWT--> [Nginx] --> [API] ----------------------
```

Não há broker de mensagens: a fila do worker é a própria tabela `leituras` (`processada = false`). Se o worker parar, as leituras se acumulam no banco e são processadas quando ele voltar.

O backend segue uma arquitetura hexagonal leve (portas e adaptadores). Os detalhes estão em [docs/backend.md](docs/backend.md).

## Tecnologias

Python 3.12, FastAPI, Uvicorn, SQLAlchemy 2, psycopg 3, Alembic, Pydantic 2, bcrypt, PostgreSQL 16 e pytest.

## Como rodar localmente

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

Para encerrar: `Ctrl+C` na API e `docker stop sspd-pg-dev` no banco.

## API disponível

| Método | Rota | Autenticação | Descrição |
|---|---|---|---|
| GET | `/health` | nenhuma | Verifica a API e o banco |
| POST | `/leituras` | header `X-API-Key` | Registra uma leitura (idempotente) |

O contrato completo, com validações e códigos de erro, está em [docs/backend.md](docs/backend.md#7-api-http).

## Variáveis de ambiente

| Variável | Usada por | Obrigatória | Descrição |
|---|---|---|---|
| `DATABASE_URL` | API, migrations, seed | sim | Conexão com o PostgreSQL. Caracteres especiais na senha vão codificados (`@` → `%40`) |
| `SENSOR_API_KEY` | API | sim | Chave exigida dos sensores |
| `CORS_ORIGINS` | API | não | Origens liberadas, separadas por vírgula |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | seed | sim | Usuário administrador criado pelo seed |
| `SEED_PACIENTES` | seed | não (padrão `50`) | Quantidade de pacientes fictícios |
| `TEST_DATABASE_URL` | testes de integração | não | Banco **descartável**: os testes apagam as tabelas |

O [.env.example](.env.example) também lista variáveis de partes ainda não implementadas (`JWT_*`, `WORKER_*` e as do simulador). Nunca commite o arquivo `.env`.

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

Último resultado (25/11/2026): **140 testes passando**, sendo 109 unitários e 31 de integração. Sem `TEST_DATABASE_URL`, os de integração são pulados.

> Nunca aponte `TEST_DATABASE_URL` para um banco com dados que você queira manter.

## Estrutura do repositório

```
backend/
├── app/
│   ├── dominio/        # entidades, regras de alerta, erros e portas
│   ├── aplicacao/      # casos de uso
│   ├── adaptadores/
│   │   ├── entrada/    # HTTP (FastAPI)
│   │   └── saida/      # PostgreSQL
│   ├── composicao.py   # liga as implementações (entrypoint da API)
│   ├── config.py       # variáveis de ambiente
│   └── seed.py         # dados iniciais
├── migrations/         # Alembic
└── tests/              # unit/ e integracao/
docs/backend.md         # documentação técnica do backend
```

## Equipe e contribuição

| Responsável | Área | Pastas |
|---|---|---|
| Gerson | API, worker, simulador, testes funcionais e script de carga | `backend/`, `simulador/`, `tests-carga/` |
| Integrante de infraestrutura | VPS, Docker, Nginx, segurança do servidor, CI/CD e deploy | `infra/`, `.github/workflows/` |
| Integrante de dashboard | Dashboard, relatório ABNT e slides | `dashboard/`, `docs/` |

Fluxo: uma branch por funcionalidade, commits pequenos em português (`feat:`, `fix:`, `test:`, `docs:`, `chore:`) e pull request para a `dev`. A `main` recebe a `dev` quando ela estiver estável.
