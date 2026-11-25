# Sistema Distribuído de Monitoramento de Pacientes

Trabalho em equipe da disciplina de computação em nuvem / sistemas distribuídos (Fametro).
Tema: **Sistema distribuído de monitoramento de pacientes — saúde digital e tolerância a falhas.**

Este arquivo dá contexto ao Claude Code. Responsável por esta parte: **Gerson** (backend: API, worker de alertas, simulador e testes funcionais).

---

## 1. Requisitos do professor (obrigatórios)

- [ ] Problema real com relevância justificada (monitoramento remoto de pacientes crônicos e necessidade de alta disponibilidade em saúde)
- [ ] Arquitetura de sistema distribuído projetada
- [ ] VPS Linux configurada
- [ ] **Pelo menos dois serviços independentes que se comuniquem por rede**
- [ ] Persistência de dados
- [ ] Mecanismos de **segurança** e de **tolerância a falhas**
- [ ] Testes **funcionais**, de **desempenho** e de **falhas**
- [ ] Procedimentos e resultados documentados (relatório ABNT no template do professor)
- [ ] Código-fonte em repositório GitHub

Entrega no Google Classroom: relatório em PDF, slides, link do GitHub, evidências técnicas (dentro do relatório) e identificação dos integrantes.

**Prazo curto:** cortar escopo sempre que possível. Funcionar e ser demonstrável vale mais do que ser sofisticado.

---

## 2. Divisão da equipe

| Pessoa | Responsabilidade | Pastas |
|---|---|---|
| Gerson | API, worker de alertas, simulador, testes funcionais, script de carga, seções do relatório sobre o backend | `/backend`, `/simulador`, `/tests-carga` |
| Amigo 1 | VPS, Docker, Nginx, HTTPS, UFW, SSH, GitHub Actions, deploy, backups, execução dos testes de desempenho e de falha | `/infra`, `.github/workflows` |
| Amigo 2 | Dashboard, montagem do relatório ABNT e slides | `/dashboard`, `/docs` |

**Regra para o Claude Code:** não alterar `/dashboard`, `/infra` nem `.github/workflows` sem pedido explícito. Se uma mudança no backend exigir algo nessas pastas (porta, variável de ambiente, healthcheck), avisar e descrever a mudança necessária.

---

## 3. Arquitetura

```
[Simulador de sensores] --HTTP POST + API key--> [Nginx] --> [API réplica 1]
                                                         \-> [API réplica 2]
                                                                  |
                                                            [PostgreSQL] <-- [Worker de alertas] (polling)
                                                                  ^
[Dashboard] --HTTP + JWT--> [Nginx] --> [API] ----------------------
```

Serviços (todos em Docker Compose):

1. **simulador**: gera leituras de N pacientes e envia para a API, com buffer local e retry.
2. **api**: recebe, valida e grava as leituras; serve os dados ao dashboard. É stateless e roda com 2 réplicas.
3. **worker**: lê as leituras não processadas, aplica as regras e grava os alertas.
4. **postgres**: persistência, com volume nomeado.
5. **migrate**: serviço one-shot que roda as migrations antes da API subir.
6. **dashboard** e **nginx**: responsabilidade dos outros integrantes.

Decisão: **sem broker de mensagens** (RabbitMQ/Redis). A fila é a própria tabela `leituras` (`processada = false`). Se o worker cair, as leituras se acumulam e são processadas quando ele volta. Isso é mais simples e ainda demonstra desacoplamento e tolerância a falhas.

---

## 4. Stack

- **Python 3.12 + FastAPI + Uvicorn**
- **SQLAlchemy 2.x** (síncrono, para simplificar) + **psycopg 3**
- **Alembic** para migrations
- **Pydantic v2** para validação e para os schemas
- **PyJWT** + **`bcrypt` direto** (sem passlib) para autenticação
- **pytest** + `fastapi.testclient` para os testes funcionais. O `TestClient` usa **`httpx2`** (o Starlette 1.10 deprecou o `httpx`)
- **PostgreSQL 16** (imagem `postgres:16-alpine` nos testes)
- **k6** (ou Locust) para o teste de carga
- Imagem base: `python:3.12-slim`. O desenvolvimento local roda no WSL2 (Ubuntu 24.04, Python 3.12, Docker Engine dentro do Ubuntu, sem Docker Desktop), com o projeto em `~/SSPD_API`
- Arquitetura do backend: **hexagonal leve (portas e adaptadores)**, com portas definidas como `typing.Protocol`. Ver seção 5.1.

> Se a equipe trocar para Spring Boot, reescrever esta seção. O resto do documento (contratos, banco, regras) vale igual.

---

## 5. Estrutura do repositório

```
/
├── CLAUDE.md
├── README.md
├── docker-compose.yml          # raiz, mantido pelo amigo da infra
├── .env.example
├── backend/
│   ├── Dockerfile              # uma imagem para api, worker e migrate
│   ├── pyproject.toml / requirements.txt
│   ├── alembic.ini
│   ├── migrations/
│   ├── app/
│   │   ├── dominio/
│   │   │   ├── entidades.py        # Leitura, Alerta, Paciente, Usuario, ResumoPaciente, ResultadoCiclo (dataclasses)
│   │   │   ├── regras.py           # avaliar(leitura) -> list[Alerta], função pura
│   │   │   ├── erros.py            # PacienteNaoEncontrado, LeituraInvalida, ...
│   │   │   └── portas.py           # Protocols (repositórios, fila de pendentes)
│   │   ├── aplicacao/
│   │   │   ├── registrar_leitura.py
│   │   │   ├── processar_pendentes.py
│   │   │   ├── reconhecer_alerta.py
│   │   │   └── consultas.py        # listar pacientes, histórico, alertas
│   │   ├── adaptadores/
│   │   │   ├── entrada/
│   │   │   │   ├── http/
│   │   │   │   │   ├── main.py     # criar_app(componentes, ...) -> FastAPI (recebe tudo pronto)
│   │   │   │   │   ├── schemas.py  # Pydantic (só aqui)
│   │   │   │   │   ├── seguranca.py# API key, JWT, hash de senha
│   │   │   │   │   ├── deps.py     # Depends que entregam os casos de uso
│   │   │   │   │   └── routers/    # leituras, pacientes, alertas, auth, health
│   │   │   │   └── worker/
│   │   │   │       └── main.py     # loop de polling (entrypoint do worker)
│   │   │   └── saida/
│   │   │       └── postgres/
│   │   │           ├── db.py       # engine, sessão, retry de conexão
│   │   │           ├── models.py   # tabelas SQLAlchemy (só aqui)
│   │   │           ├── mapeamento.py   # model <-> entidade
│   │   │           ├── repositorios.py
│   │   │           └── fila_pendentes.py
│   │   ├── config.py               # variáveis de ambiente
│   │   ├── composicao.py           # monta adaptadores + casos de uso (DI manual); criar_app_http é o entrypoint do uvicorn
│   │   └── seed.py                 # cria usuário admin e pacientes fictícios (idempotente)
│   └── tests/
│       ├── unit/                   # domínio, aplicação e HTTP (test_http.py), com fakes em memória
│       ├── fakes.py                # implementações em memória das portas
│       └── integracao/             # adaptadores Postgres e rotas HTTP, banco real (apoio.py = helpers)
├── simulador/
│   ├── Dockerfile
│   └── simulador.py
├── tests-carga/
│   └── leituras.js             # script k6
├── dashboard/                  # amigo 2
├── infra/                      # amigo 1
└── docs/                       # relatório, prints, gráficos
```

### 5.1 Arquitetura do backend: hexagonal leve

**Objetivo:** separar as responsabilidades por contratos (portas). A API HTTP e o worker são dois **adaptadores de entrada** diferentes que usam o mesmo núcleo (domínio + aplicação). O Postgres é um **adaptador de saída**.

```
   [FastAPI / HTTP] ──┐                         ┌── [Postgres: repositórios]
                      ├─> aplicacao ─> dominio <┤
   [Worker / polling]─┘   (casos de uso) (portas)└── [Postgres: fila de pendentes]
   adaptadores de entrada                           adaptadores de saída
```

**Regra de dependência (obrigatória):**

| Camada | Pode importar | Não pode importar |
|---|---|---|
| `dominio` | só a stdlib | `aplicacao`, `adaptadores`, FastAPI, SQLAlchemy, Pydantic |
| `aplicacao` | `dominio` | `adaptadores`, FastAPI, SQLAlchemy, Pydantic |
| `adaptadores/entrada` | `aplicacao`, `dominio` | `adaptadores/saida` (recebe tudo pronto via `composicao.py`) |
| `adaptadores/saida` | `dominio` | `aplicacao`, `adaptadores/entrada` |
| `composicao.py` | tudo | é o único lugar que conhece as implementações concretas |

Se for preciso quebrar essa regra, parar e perguntar antes.

**Portas (`dominio/portas.py`):**

```python
from typing import Protocol, ContextManager
from datetime import datetime

class RepositorioPacientes(Protocol):
    def existe(self, paciente_id: str) -> bool: ...
    def obter(self, paciente_id: str) -> Paciente | None: ...
    def listar_com_resumo(self) -> list[ResumoPaciente]: ...
    def criar(self, paciente: Paciente) -> None: ...

class RepositorioLeituras(Protocol):
    def salvar_se_nova(self, leitura: Leitura) -> bool: ...   # False = id já existia
    def ultima(self, paciente_id: str) -> Leitura | None: ...
    def historico(self, paciente_id: str, desde: datetime | None,
                  ate: datetime | None, limite: int) -> list[Leitura]: ...

class RepositorioAlertas(Protocol):
    def listar(self, status: str | None, paciente_id: str | None,
               limite: int) -> list[Alerta]: ...
    def obter(self, alerta_id: int) -> Alerta | None: ...
    def atualizar_status(self, alerta_id: int, status: str) -> None: ...

class RepositorioUsuarios(Protocol):
    def por_email(self, email: str) -> Usuario | None: ...

class LotePendente(Protocol):
    leituras: list[Leitura]
    def tipos_abertos(self, paciente_id: str) -> set[str]: ...  # para o anti-spam
    def registrar_alertas(self, alertas: list[Alerta]) -> None: ...
    def marcar_processadas(self) -> None: ...

class FilaPendentes(Protocol):
    def reservar(self, limite: int) -> ContextManager[LotePendente]: ...
    def contar_pendentes(self) -> int: ...  # tamanho do backlog, para o log do worker
```

**Contrato de `FilaPendentes.reservar` (crítico para a tolerância a falhas):**
- Ao entrar: abre a transação e executa `SELECT ... WHERE processada = false ORDER BY ts LIMIT :limite FOR UPDATE SKIP LOCKED`.
- Tudo o que o `LotePendente` fizer acontece **na mesma transação**.
- Saída sem exceção → `COMMIT`. Saída com exceção → `ROLLBACK`, e o lote volta a ficar pendente.
- O caso de uso nunca vê SQL, sessão ou transação.

O `salvar_se_nova` usa `INSERT ... ON CONFLICT (id) DO NOTHING` e devolve se inseriu. A idempotência é semântica da porta; o SQL fica no adaptador.

**Casos de uso (`aplicacao/`):** classes pequenas que recebem as portas no construtor e expõem um método `executar(...)`.

```python
class RegistrarLeitura:
    def __init__(self, pacientes: RepositorioPacientes, leituras: RepositorioLeituras,
                 relogio: Callable[[], datetime]): ...
    def executar(self, leitura: Leitura) -> bool:   # True = nova, False = duplicada
        # valida ts com relogio() (dominio), garante paciente existente, salvar_se_nova

class ProcessarPendentes:
    def __init__(self, fila: FilaPendentes): ...
    def executar(self, limite: int) -> ResultadoCiclo:  # processadas, alertas_criados, backlog
        # with fila.reservar(limite) as lote: avaliar regras, anti-spam, registrar, marcar
        # depois do commit: backlog = fila.contar_pendentes()
```

**Onde mora cada coisa:**
- Validação de faixa (fc, spo2, temp) é regra de domínio e fica no `__post_init__` da entidade, de modo que uma `Leitura` inválida não chega a existir. O Pydantic do adaptador HTTP só valida tipo e formato.
- A regra "`ts` no máximo 5 min no futuro" depende do relógio. Por isso fica em `Leitura.validar_horario(agora)`, e `RegistrarLeitura` recebe um `relogio: Callable[[], datetime]` no construtor. Assim o teste não precisa de mock de `datetime`. A regra não fica no `__post_init__` porque uma leitura antiga reenviada pelo buffer continua válida.
- Erros de domínio (`PacienteNaoEncontrado`, `LeituraInvalida`) são exceções do domínio. O adaptador HTTP converte em 404/422 com um exception handler.
- JWT, API key, bcrypt e CORS são preocupação do adaptador HTTP e **não** têm porta.
- O `/health` não tem caso de uso. O `composicao.py` injeta na rota uma função `verificar_banco() -> bool`, implementada em `db.py`. A rota não importa o adaptador de saída.
- O adaptador HTTP recebe do `composicao.py` um `Componentes` (dataclass em `http/deps.py`, guardado em `app.state`) com os casos de uso e o `verificar_banco`. Para adicionar um caso de uso a uma rota: campo novo em `Componentes`, uma função em `deps.py` e a montagem em `composicao.montar_componentes_http`.
- O HTTP não importa SQLAlchemy. As exceções que significam "banco fora" (`OperationalError` e `TimeoutError` do pool) chegam por `criar_app(..., erros_indisponibilidade=...)` e viram 503.
- O `seed.py` é um entrypoint de infraestrutura, como o `composicao.py`, e por isso fala direto com o adaptador Postgres.
- O teste `tests/unit/test_arquitetura.py` verifica a regra de dependência via `ast`, para todas as camadas da tabela acima.
- Logs de ciclo do worker ficam no adaptador de entrada `worker/`, a partir do `ResultadoCiclo` (que já traz o `backlog`).

**Fora de escopo nesta arquitetura (não fazer):**
- Container ou framework de injeção de dependência. Composição manual em `composicao.py` + `Depends` do FastAPI.
- DTO por camada. O schema Pydantic converte direto para a entidade e de volta.
- Porta para trocar de banco "no futuro". As portas existem para testar e para separar responsabilidades.
- Interfaces com uma única função trivial só por formalidade.

---

## 6. Variáveis de ambiente

| Variável | Usada por | Exemplo |
|---|---|---|
| `DATABASE_URL` | api, worker, migrate | `postgresql+psycopg://monitor:senha@postgres:5432/monitor` |
| `JWT_SECRET` | api | string aleatória longa |
| `JWT_EXP_MINUTES` | api | `60` |
| `SENSOR_API_KEY` | api, simulador | string aleatória |
| `CORS_ORIGINS` | api | `https://dominio-do-dashboard` |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | seed | criados no primeiro start |
| `SEED_PACIENTES` | seed | `50` (subir para 500 no teste de carga) |
| `WORKER_INTERVALO` | worker | `2` (segundos) |
| `WORKER_LOTE` | worker | `500` |
| `API_URL` | simulador | `http://nginx/leituras` |
| `N_PACIENTES` / `INTERVALO` | simulador | `10` / `2` |
| `TEST_DATABASE_URL` | só testes de integração | `postgresql+psycopg://monitor:teste@localhost:55432/monitor_teste` |

Nunca commitar `.env`. Manter o `.env.example` atualizado sempre que surgir uma variável nova.

- Senha com caractere especial na `DATABASE_URL` precisa ir codificada (`@` → `%40`, `%` → `%25`). O `migrations/env.py` passa a URL direto ao SQLAlchemy, sem passar pelo `alembic.ini`, justamente para aceitar `%`.
- `TEST_DATABASE_URL` aponta para um banco **descartável**: os testes apagam as tabelas. Nunca apontar para produção. Sem ela, os testes de integração são pulados (não falham).
- Sem `SENSOR_API_KEY` a API não sobe (erro de configuração). Sem `DATABASE_URL`, nenhum serviço sobe.

---

## 7. Modelo de dados

```sql
pacientes (
  id           VARCHAR(10) PRIMARY KEY,      -- "P001", mesmo código usado pelo simulador
  nome         VARCHAR(100) NOT NULL,        -- fictício
  leito        VARCHAR(20),
  criado_em    TIMESTAMPTZ DEFAULT now()
)

leituras (
  id           UUID PRIMARY KEY,             -- gerado pelo SENSOR (idempotência)
  paciente_id  VARCHAR(10) NOT NULL REFERENCES pacientes(id),
  fc           SMALLINT NOT NULL,            -- bpm
  spo2         NUMERIC(4,1) NOT NULL,        -- %
  temp         NUMERIC(3,1) NOT NULL,        -- °C
  ts           TIMESTAMPTZ NOT NULL,         -- hora da medição (sensor)
  recebido_em  TIMESTAMPTZ DEFAULT now(),    -- hora de chegada (servidor)
  processada   BOOLEAN NOT NULL DEFAULT false
)
-- índices: (paciente_id, ts DESC) e um índice parcial WHERE processada = false

alertas (
  id           BIGSERIAL PRIMARY KEY,
  paciente_id  VARCHAR(10) NOT NULL REFERENCES pacientes(id),
  leitura_id   UUID NOT NULL REFERENCES leituras(id),
  tipo         VARCHAR(30) NOT NULL,         -- SPO2_BAIXA, TAQUICARDIA, BRADICARDIA, FEBRE, HIPOTERMIA
  valor        NUMERIC(5,1) NOT NULL,
  severidade   VARCHAR(10) NOT NULL,         -- ALTA, MEDIA
  status       VARCHAR(15) NOT NULL DEFAULT 'aberto',  -- aberto | reconhecido
  criado_em    TIMESTAMPTZ DEFAULT now()
)
-- índices: (status, criado_em DESC) e o ÚNICO PARCIAL uq_alertas_aberto_por_tipo
--   (paciente_id, tipo) WHERE status = 'aberto'  -> no máximo um alerta aberto por paciente e tipo

usuarios (
  id           SERIAL PRIMARY KEY,
  email        VARCHAR(120) UNIQUE NOT NULL,
  senha_hash   VARCHAR(200) NOT NULL
)
```

`recebido_em - ts` fornece a latência ponta a ponta, que é útil no relatório.

Implementado na migration `migrations/versions/0001_inicial.py`. Diferenças em relação ao esboço acima: `criado_em` e `recebido_em` são `NOT NULL` (com default), e o índice único parcial dos alertas garante o anti-spam no próprio banco, mesmo com mais de um worker. O teste `test_migrations_batem_com_os_models` falha se `models.py` mudar sem migration nova.

O banco guarda `spo2` e `temp` com uma casa decimal (`NUMERIC`), então 97.43 vira 97.4. O worker avalia o valor já arredondado.

---

## 8. Contrato da API

Respostas em JSON. Erros no formato padrão do FastAPI (`{"detail": ...}`). Timestamps em ISO 8601 UTC.

### Autenticação
- **Sensores:** header `X-API-Key: <SENSOR_API_KEY>`. Só vale para `POST /leituras`.
- **Dashboard:** `Authorization: Bearer <JWT>`, obtido em `POST /auth/login`.

### Endpoints

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| GET | `/health` | nenhuma | Executa `SELECT 1` no banco. Responde 200 `{"status":"ok","db":"ok"}` ou 503 `{"status":"degradado","db":"indisponivel"}` se o banco estiver fora |
| POST | `/auth/login` | nenhuma | `{"email","senha"}` → `{"access_token","token_type":"bearer"}` |
| POST | `/leituras` | API key | Grava uma leitura. Idempotente |
| GET | `/pacientes` | JWT | Lista os pacientes com a última leitura e a quantidade de alertas abertos |
| POST | `/pacientes` | JWT | Cria um paciente |
| GET | `/pacientes/{id}/ultima` | JWT | Última leitura do paciente |
| GET | `/pacientes/{id}/leituras?desde=&ate=&limit=` | JWT | Histórico, ordenado por `ts` desc. `limit` padrão 100, máximo 1000 |
| GET | `/alertas?status=aberto&paciente_id=&limit=` | JWT | Lista de alertas |
| PATCH | `/alertas/{id}` | JWT | `{"status":"reconhecido"}`. Idempotente: reconhecer um alerta já reconhecido responde 200. Alerta inexistente → 404 |

### `POST /leituras`

Request:
```json
{
  "id": "6f1c2e7a-3b2d-4c4e-9a51-0b8f3a1d2c77",
  "paciente_id": "P001",
  "fc": 82,
  "spo2": 97.4,
  "temp": 36.7,
  "ts": "2026-11-25T14:03:12.481Z"
}
```

Validação (rejeitar com 422):
- `fc`: 20–250
- `spo2`: 50–100
- `temp`: 30–43
- `ts`: no máximo 5 min no futuro
- `paciente_id`: precisa existir (senão 404)

Respostas:
- `201` quando a leitura é nova
- `200` quando o `id` já existia (reenvio do buffer). Usar `INSERT ... ON CONFLICT (id) DO NOTHING` e **não** retornar erro, porque o simulador trata qualquer 2xx como sucesso.
- `401` quando a API key é inválida ou ausente (`{"detail":"API key inválida ou ausente"}`). A API key é checada antes da validação dos campos: sem chave, um corpo com campo inválido dá 401 (JSON malformado ainda dá 422)
- `404` quando o paciente não existe
- `422` para valor fora da faixa ou `ts` no futuro, **no mesmo formato dos 422 do FastAPI**: `{"detail":[{"loc":["body","<campo>"],"msg":"...","type":"value_error"}]}`. Também para formato inválido (`ts` sem fuso, `id` que não é UUID, `fc` não inteiro, campo faltando, JSON quebrado)
- `503` com `Retry-After: 5` quando o banco está fora (`{"detail":"banco de dados indisponível, tente novamente"}`)
- `500` só para bug, sempre `{"detail":"erro interno"}`, sem stack trace nem mensagem da exceção

Corpo das respostas 200/201: `{"id": "<uuid>", "nova": true|false}`.

---

## 9. Worker de alertas

O adaptador de entrada `adaptadores/entrada/worker/main.py` chama `ProcessarPendentes.executar(WORKER_LOTE)` a cada `WORKER_INTERVALO` segundos. Dentro do caso de uso:

1. `with fila.reservar(limite) as lote:` (o adaptador abre a transação e trava as pendentes com `FOR UPDATE SKIP LOCKED`; o `SKIP LOCKED` permite rodar mais de um worker sem processar a mesma leitura duas vezes).
2. Para cada leitura do lote, `regras.avaliar(leitura)`, descartando os tipos que já estão em `lote.tipos_abertos(paciente_id)` **ou que já foram gerados neste mesmo lote** para o paciente (o caso de uso acumula esses tipos, porque `tipos_abertos` só enxerga o que já está no banco).
3. `lote.registrar_alertas(...)` e `lote.marcar_processadas()`.
4. Saída do `with`: commit. Se houver exceção, rollback e o lote é reprocessado no ciclo seguinte.

Regras (em `dominio/regras.py`, como funções puras):

| Tipo | Condição | Severidade |
|---|---|---|
| SPO2_BAIXA | spo2 < 90 | ALTA |
| TAQUICARDIA | fc > 120 | MEDIA |
| BRADICARDIA | fc < 50 | MEDIA |
| FEBRE | temp ≥ 38.0 | MEDIA |
| HIPOTERMIA | temp < 35.0 | ALTA |

Anti-spam: não criar um alerta novo se já existir um alerta `aberto` do mesmo `tipo` para o mesmo paciente.

Logar em cada ciclo: quantidade processada, quantidade de alertas criados e tamanho do backlog (`ResultadoCiclo.backlog`, obtido com `fila.contar_pendentes()` depois do commit). Esse log é a evidência do teste de falha do worker.

Encerrar de forma limpa ao receber SIGTERM (`docker stop`).

---

## 10. Tolerância a falhas (parte do backend)

1. **Idempotência** no `POST /leituras` (UUID gerado pelo sensor + `ON CONFLICT DO NOTHING`).
2. **Buffer + retry no simulador**: quando a API está fora, as leituras ficam na fila local e são reenviadas depois.
3. **API stateless** (JWT, sem sessão): permite 2 réplicas atrás do Nginx.
4. **Retry de conexão com o banco no startup** da API e do worker (até ~30 s, com backoff), para que nenhum dos dois morra se subir antes do Postgres.
5. **Worker desacoplado**: uma queda do worker não afeta a ingestão, e o backlog é processado quando ele volta.
6. **`/health` real** (testa o banco), usado no healthcheck do Compose e no smoke test do deploy.
7. **Migrations em serviço separado** (`migrate`), para evitar que duas réplicas da API rodem migrations ao mesmo tempo.

Como foi implementado (e verificado em ensaio local com uvicorn real, duas réplicas e `docker stop`/`start` no Postgres):
- **Retry no startup** (`db.aguardar_banco`): backoff 0,5 → 1 → 2 → 4 → 5 s, até ~30 s. Usado pela API, pelo `migrate` (`env.py`) e pelo seed.
- **Modo degradado**: se o banco não voltar nos 30 s, a API sobe mesmo assim e o `/health` responde 503 até ele voltar, em vez de o container reiniciar em loop.
- **Recuperação sem reiniciar**: `pool_pre_ping=True` descarta as conexões mortas depois de um restart do Postgres. No ensaio, a API voltou a responder 200 cerca de 1 s depois do Postgres.
- **`connect_timeout=5`**: com o banco fora, `/health` e `POST /leituras` respondem rápido (503) em vez de travar.
- **Logs legíveis durante a queda**: banco fora gera uma linha de log por requisição, sem traceback, para o log continuar servindo de evidência.
- **Anti-spam com vários workers**: além do `SKIP LOCKED`, o índice único parcial impede dois alertas abertos iguais. Os alertas são inseridos em ordem fixa (paciente, tipo) para evitar deadlock. Efeito colateral aceito: numa disputa entre workers, o log de um deles pode contar 1 alerta a mais do que foi gravado.
- **SIGTERM**: a API fecha o pool de conexões no shutdown (lifespan).

Cenários de teste de falha esperados (executados pelo amigo da infra, com explicação do Gerson):

| Cenário | Comportamento esperado | Evidência |
|---|---|---|
| `docker stop` em uma réplica da API | A outra continua atendendo e o Nginx redistribui | Log do Nginx e do simulador sem erros |
| `docker stop` nas duas réplicas | O buffer do simulador cresce. Ao voltar, esvazia sem duplicar nada | Contagem de leituras antes e depois, `SELECT count(*)`, `SELECT count(DISTINCT id)` |
| `docker stop` no worker | A ingestão continua e o backlog cresce. Ao voltar, o backlog é processado | Log do worker e `SELECT count(*) WHERE processada=false` ao longo do tempo |
| `docker stop` no Postgres | A API responde 503 no `/health` e o simulador bufferiza. Ao voltar, tudo se recupera | Tempo até a recuperação |

---

## 11. Segurança (parte do backend)

- API key para os sensores e JWT para os usuários, com senha em bcrypt
- Validação de entrada com Pydantic (faixas da seção 8)
- CORS restrito a `CORS_ORIGINS`
- Segredos apenas em variáveis de ambiente
- Nada de stack trace na resposta em produção
- Dados de pacientes **fictícios**. Justificativa no relatório: a LGPD (Lei 13.709/2018, art. 5º, II, e art. 11) classifica dados de saúde como dados pessoais sensíveis
- Container rodando com usuário não-root no Dockerfile

(UFW, SSH, HTTPS, fail2ban e rate limit no Nginx ficam com o amigo da infra.)

---

## 12. Simulador

Base: script Python com uma thread por paciente, variação por random walk, anomalia ocasional de SpO₂ (~2%) e buffer `deque(maxlen=1000)` com retry.

Ajustes necessários em relação à versão inicial:
- Gerar `id` com `uuid.uuid4()` **no momento da criação da leitura**, e não no envio
- Enviar `X-API-Key` em vez de `Authorization: Bearer`
- Tratar `200` e `201` como sucesso
- Log com a quantidade enviada, o tamanho do buffer e as falhas, para as evidências
- Opcional: perfis de paciente (estável, febril, hipoxêmico)

---

## 13. Testes

### Unitários (`tests/unit`), sem banco
Usam os fakes em memória de `tests/fakes.py`, que implementam as portas. Rodam em milissegundos.
- `regras.avaliar` com casos de borda (spo2 = 90 não alerta, 89.9 alerta etc.)
- Validação de faixa das entidades
- `RegistrarLeitura`: leitura nova → True; mesmo `id` → False; paciente inexistente → `PacienteNaoEncontrado`; `ts` mais de 5 min no futuro (relógio fake) → `LeituraInvalida`
- `ProcessarPendentes`: pendentes → alertas criados e leituras marcadas; anti-spam contra alertas abertos no banco **e** dentro do mesmo lote; exceção no meio → nada marcado (o fake de `FilaPendentes` simula o rollback); `backlog` preenchido
- `ReconhecerAlerta`: alerta inexistente → erro de domínio; alerta já reconhecido → sem erro
- Regra de dependência: um teste lê os imports de `dominio/` e `aplicacao/` (via `ast`) e falha se houver import proibido

Os fakes também precisam cumprir o contrato das portas (o fake da fila descarta as mudanças se houver exceção dentro do `with`).

### Integração (`tests/integracao`), com Postgres real, rodando no GitHub Actions
- Adaptadores Postgres: `salvar_se_nova` duas vezes com o mesmo id → uma linha só; `reservar` com dois workers concorrentes → nenhuma leitura processada duas vezes; exceção dentro do `with` → rollback
- HTTP (`TestClient`): `POST /leituras` válida → 201; reenvio → 200; API key errada → 401; fora da faixa → 422; paciente inexistente → 404
- Login correto → token; login errado → 401; rota protegida sem token → 401
- `GET /pacientes/{id}/leituras` respeita `limit` e a ordenação
- `/health` → 200 com o banco no ar

O banco de teste é um Postgres real (serviço `postgres` no workflow do Actions ou via Compose local). Não usar SQLite, porque `ON CONFLICT`, `SKIP LOCKED` e `TIMESTAMPTZ` têm comportamento diferente.

### Como rodar

```bash
# Postgres descartável (dados em memória), uma vez:
docker run -d --name sspd-pg-teste --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=monitor -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=monitor_teste \
  -p 127.0.0.1:55432:5432 postgres:16-alpine

cd backend
export TEST_DATABASE_URL=postgresql+psycopg://monitor:teste@localhost:55432/monitor_teste
.venv/bin/python -m pytest -q                    # tudo
.venv/bin/python -m pytest -q -m "not integracao" # só unitários
```

- O esquema dos testes é criado pela própria migration (`alembic downgrade base` + `upgrade head`), não por `create_all`, para testar a migration.
- Os testes de integração são marcados `integracao` automaticamente pelo `conftest.py`.
- O teste de reservas simultâneas usa `lock_timeout` para falhar em 2 s, em vez de pendurar o CI, se alguém tirar o `SKIP LOCKED`.
- Os testes foram conferidos quebrando o código de propósito (anti-spam, marcação, `SKIP LOCKED`, `ON CONFLICT`): em todos os casos algum teste falha.

### Desempenho (script k6 em `/tests-carga`)
- Cenários com 10, 100 e 500 sensores virtuais fazendo `POST /leituras`
- Métricas: latência p50/p95/p99, requisições por segundo, taxa de erro
- CPU/RAM da VPS coletados pelo amigo da infra (`docker stats`)
- Exportar o resumo em JSON para gerar a tabela e o gráfico do relatório

---

## 14. Convenções

- Código e comentários em português (nomes de domínio: `leitura`, `paciente`, `alerta`), exceto termos técnicos consagrados
- Logs em stdout, uma linha por evento, com timestamp
- Commits pequenos (um por unidade lógica), mensagem em português, prefixos `feat:`, `fix:`, `test:`, `docs:`, `chore:`
- Branch por funcionalidade e PR para a **`dev`** (não para a `main`). A `main` recebe a `dev` quando ela estiver estável. O histórico mostra a contribuição de cada um
- Remote: `SSDP_API` → `https://github.com/fabi0jr/SSPD_API.git`. O terminal do Claude Code não tem credencial do GitHub: o push é feito pelo Gerson no VS Code
- **Nunca commitar `backend/.venv`** (nem usar "adicionar tudo" sem conferir o `git status`). Um `.venv` commitado faz o git apagar o venv local de quem troca de branch. Se o venv sumir: `python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements-dev.txt`
- Swagger disponível em `/docs`. Manter os schemas com `examples`, porque o amigo do dashboard usa o Swagger como contrato

---

## 15. Ordem de implementação sugerida

| Etapa | Situação | Branch |
|---|---|---|
| 1. Domínio | ✅ feita, na `dev` | (commit inicial `42ccb14`) |
| 2. Fakes + `RegistrarLeitura` e `ProcessarPendentes` | ✅ feita, na `dev` (PR #1) | `feat/casos-de-uso` |
| 3. Adaptador Postgres | ✅ feita, na `dev` (PR #3) | `feat/adaptador-postgres` |
| 4. `composicao.py` + HTTP (`/health`, `POST /leituras`) | ✅ feita, na `dev` (PR #5) | `feat/api-http` |
| 5 a 8 | a fazer | |
| 9. Textos do backend | parcial: cobre as etapas 1 a 4, atualizar a cada etapa nova | `docs/readme` |

O PR #2 (`fix/restaurar-dominio`) restaurou o domínio, que o commit `e3af254` tinha apagado junto com o commit do `.venv`.

Documentação: `README.md` (estado atual e como rodar) e `docs/backend.md` (detalhes técnicos). Ao terminar uma etapa, atualizar os dois.

1. `dominio/`: entidades, validação, erros, `regras.py` e `portas.py`, já com os testes unitários das regras
2. `tests/fakes.py` + casos de uso `RegistrarLeitura` e `ProcessarPendentes`, com testes unitários
3. Adaptador Postgres: `db.py` com retry, models, migration inicial (Alembic), repositórios, `FilaPendentes` e `seed.py`, com testes de integração
4. `composicao.py` + adaptador HTTP: `/health` e `POST /leituras` com API key (publicar o Swagger cedo para o dashboard)
5. Auth (login + JWT), as rotas GET de pacientes, leituras e alertas e o `PATCH` de alertas (casos de uso de consulta e `ReconhecerAlerta`)
6. Adaptador de entrada do worker (loop, logs, SIGTERM)
7. Dockerfile do backend (uma imagem com comandos diferentes para api, worker e migrate) + Dockerfile do simulador e ajustes no simulador
8. Script k6
9. Textos das seções do relatório sobre o backend em `/docs/backend.md`: arquitetura hexagonal (portas e adaptadores, regra de dependência, diagrama da seção 5.1), modelagem, regras de alerta, mecanismos de tolerância a falhas e de segurança da aplicação

### Comandos dos serviços (para o Dockerfile e o Compose)

Todos rodam dentro de `backend/`:

| Serviço | Comando |
|---|---|
| migrate | `alembic upgrade head && python -m app.seed` |
| api | `uvicorn app.composicao:criar_app_http --factory --host 0.0.0.0 --port 8000 --proxy-headers` |
| worker | (etapa 6) |

Pendências para a infra, a resolver na etapa 7 (avisar o amigo da infra, não editar `/infra`):
- A imagem `python:3.12-slim` não tem `curl`: o healthcheck da API precisa usar `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"`.
- O Nginx precisa repassar `/docs` e `/openapi.json` para a API, para o Swagger funcionar na VPS.
- `--proxy-headers` porque a API fica atrás do Nginx.
- Os logs de acesso do uvicorn saem sem timestamp. Ajustar com `--log-config` no Dockerfile.
- GitHub Actions: serviço `postgres:16` e a variável `TEST_DATABASE_URL` para os testes de integração.

## 16. Fora de escopo (não fazer, a menos que sobre tempo)

- RabbitMQ, Redis ou Kafka
- Notificação por e-mail, SMS ou Telegram
- WebSocket/SSE (opcional no fim: `/alertas/stream`)
- Multi-tenant, perfis de usuário ou RBAC
- Score clínico (NEWS2 etc.)
- ORM assíncrono
