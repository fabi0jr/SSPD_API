# Protocolo de integração: infra ↔ backend

O que o backend precisa da infraestrutura (Docker Compose, Nginx, VPS e CI) e o que ele oferece em troca. Estado em 25/11/2026, etapa 7 do backend. O `docker-compose.yml`, o Nginx e os workflows são mantidos pela infra; este arquivo só descreve o contrato.

## 1. Serviços do backend

Duas imagens, ambas `python:3.12-slim` com usuário não-root (`uid 10001`):

| Imagem | Build | Serviços |
|---|---|---|
| backend | `build: ./backend` (`backend/Dockerfile`) | `api`, `worker` e `migrate`: a **mesma imagem**, só muda o comando |
| simulador | `build: ./simulador` (`simulador/Dockerfile`) | `simulador` |

| Serviço | Comando | Situação |
|---|---|---|
| `migrate` (one-shot) | `sh -c "alembic upgrade head && python -m app.seed"` | ✅ pronto |
| `api` (2 réplicas) | o **padrão da imagem** (não declarar `command`) | ✅ pronto |
| `worker` (1 réplica) | `["python", "-m", "app.composicao", "worker"]` | ✅ pronto |
| `simulador` (1 réplica) | o padrão da imagem (`python simulador.py`) | ✅ pronto |

O comando padrão da imagem do backend é:

```
uvicorn app.composicao:criar_app_http --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=* --log-config uvicorn-log.json
```

O `--log-config` põe data e hora nos logs do uvicorn, inclusive nos de acesso. Se o Compose sobrescrever o comando da API, ele precisa manter essa opção. O `--forwarded-allow-ips=*` faz a API confiar no `X-Forwarded-For` de qualquer origem; isso é seguro **só porque a porta 8000 não é publicada** (a API só é alcançada pelo Nginx, dentro da rede do Compose). Não use `ports:` na `api`.

A imagem não declara `HEALTHCHECK`, porque o worker usa a mesma imagem e não tem porta. O healthcheck da API fica no Compose (seção 3).

O comando do `worker` (e o do simulador, que já é o padrão da imagem) precisa ficar na **forma exec** (lista no YAML, `["python", "-m", "app.composicao", "worker"]`), sem `sh -c`. Assim o Python é o processo principal do container e recebe o `SIGTERM` do `docker stop`; um `sh -c` não repassa o sinal, e o Docker acaba matando o worker com `SIGKILL` depois de 10 s. O worker não tem porta nem `/health`: basta `restart: unless-stopped`, sem healthcheck.

### Ordem de subida

```
postgres (healthy) ──> migrate (completed_successfully) ──> api ×2, worker
```

- Só o `migrate` roda migrations; a API **nunca** roda. Assim, duas réplicas não aplicam a mesma migration ao mesmo tempo.
- O seed é idempotente: pode rodar em todo deploy, porque o que já existe não é alterado.
- O worker depende só do `migrate`, não da API. Com o banco fora, ele não encerra: tenta de novo a cada ciclo, sem prazo.
- API, migrate e seed tentam conectar ao banco por cerca de 30 s (0,5 s → 5 s). Se o banco não voltar, a API **sobe mesmo assim** em modo degradado (`/health` = 503) e se recupera sozinha quando ele voltar. Não é preciso `restart` em loop por causa do banco.

## 2. Variáveis de ambiente

| Variável | `api` | `migrate` | `worker` | Obrigatória | Observação |
|---|---|---|---|---|---|
| `DATABASE_URL` | ✔ | ✔ | ✔ | sim | `postgresql+psycopg://usuario:senha@postgres:5432/monitor`. Caractere especial na senha vai codificado (`@` → `%40`, `%` → `%25`) |
| `SENSOR_API_KEY` | ✔ | | | sim | a mesma no simulador. Gerar com `openssl rand -hex 32` |
| `JWT_SECRET` | ✔ | | | sim | **no mínimo 32 caracteres**, senão a API não sobe. **O mesmo valor nas duas réplicas**, senão um token de uma é recusado pela outra. `openssl rand -hex 32` |
| `JWT_EXP_MINUTES` | ✔ | | | não (60) | validade do token |
| `CORS_ORIGINS` | ✔ | | | não | origem(ns) do dashboard, separadas por vírgula, ex. `https://dominio-do-dashboard`. Vazio = nenhuma origem cruzada liberada |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | | ✔ | | sim | usuário do dashboard criado pelo seed |
| `SEED_PACIENTES` | | ✔ | | não (50) | 500 no teste de carga |
| `WORKER_INTERVALO` | | | ✔ | não (2) | segundos entre os ciclos |
| `WORKER_LOTE` | | | ✔ | não (500) | máximo de leituras por ciclo |

Simulador:

| Variável | Obrigatória | Observação |
|---|---|---|
| `API_URL` | sim | URL completa do `POST /leituras` **pelo Nginx**, ex. `http://nginx/leituras` (o nome do serviço no Compose). Apontar para o Nginx, e não para uma réplica, é o que permite o teste de falha de uma réplica |
| `SENSOR_API_KEY` | sim | o mesmo valor da `api` |
| `N_PACIENTES` | não (10) | **no máximo `SEED_PACIENTES`**: os sensores usam `P001`, `P002`... do seed; paciente inexistente dá 404 e a leitura é descartada |
| `INTERVALO` | não (2) | segundos entre as leituras de cada sensor; aceita fração. Carga gerada: `N_PACIENTES / INTERVALO` requisições por segundo |

Referência com todas as variáveis: [.env.example](../.env.example). Segredos só no `.env` da VPS ou nos secrets do GitHub, nunca commitados. A aplicação não lê o arquivo `.env` sozinha: o Compose precisa repassá-lo (`env_file`).

Sem `DATABASE_URL`, `SENSOR_API_KEY` ou `JWT_SECRET` válido (ou, no worker, com `WORKER_INTERVALO` ou `WORKER_LOTE` menor ou igual a zero), o processo sai com erro de configuração já no start. O erro aparece no log com o nome da variável.

## 3. Healthcheck

`GET /health` executa `SELECT 1` no banco:

| Resposta | Significado |
|---|---|
| 200 `{"status":"ok","db":"ok"}` | API e banco no ar |
| 503 `{"status":"degradado","db":"indisponivel"}` | API no ar, banco fora |

A imagem `python:3.12-slim` não tem `curl`. Healthcheck sugerido no Compose:

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
  interval: 10s
  timeout: 5s
  retries: 3
  start_period: 40s   # cobre os ~30 s de espera pelo banco
```

`urlopen` falha com 503, então o container fica `unhealthy` enquanto o banco estiver fora. Isso é esperado e é a evidência do teste de falha do Postgres.

### 3.1 Simulador

O simulador não tem porta nem healthcheck. Ele pode subir antes da API: até ela responder, as leituras ficam no buffer e são reenviadas depois. Basta `restart: unless-stopped` e `depends_on: [nginx]` (sem condição). Se o `API_URL` usar `https`, o certificado precisa ser válido para o nome usado; dentro da rede do Compose, `http://nginx/leituras` evita isso.

Esboço (o arquivo é da infra; nomes e redes podem mudar):

```yaml
simulador:
  build: ./simulador
  env_file: .env          # API_URL, SENSOR_API_KEY, N_PACIENTES, INTERVALO
  depends_on: [nginx]
  restart: unless-stopped
```

## 4. Nginx

Rotas que o Nginx deve repassar para o upstream das réplicas (porta 8000):

| Prefixo | Quem usa |
|---|---|
| `/leituras` | simulador (header `X-API-Key`) |
| `/auth/`, `/pacientes`, `/alertas` | dashboard (header `Authorization`) |
| `/health` | healthcheck e smoke test |
| `/docs`, `/redoc`, `/openapi.json` | Swagger (o dashboard usa como contrato) |

Recomendações:

- Repassar `Host`, `X-Forwarded-For` e `X-Forwarded-Proto` (a API roda com `--proxy-headers`).
- Não remover os headers `Authorization` e `X-API-Key`.
- **Rate limit** mais apertado em `POST /auth/login` (força bruta de senha, ex. 5 req/min por IP) e mais folgado em `/leituras` (os sensores mandam a cada `INTERVALO` segundos; no teste de carga são 500 sensores).
- **Retentativa entre réplicas:** o `POST /leituras` e o `PATCH /alertas/{id}` são idempotentes, então é seguro o Nginx reenviar para a outra réplica quando uma cai no meio da requisição (`proxy_next_upstream error timeout http_502 http_503 non_idempotent;` no `location /leituras`). Não usar `non_idempotent` em `POST /pacientes` (o reenvio dá 409).
- Se o dashboard for servido pelo mesmo domínio, o CORS não é necessário; se for outro domínio, ele precisa estar em `CORS_ORIGINS`.
- O Postgres **não** deve ser exposto fora da rede do Compose.

## 5. Encerramento (SIGTERM)

No `docker stop`, a API termina as requisições em andamento e fecha o pool de conexões. O padrão de 10 s do Docker basta. O worker trata `SIGTERM`: termina o ciclo em andamento (a transação chega ao commit) e sai com código 0 em menos de 1 s, sem esperar o intervalo. Isso vale só com o comando na forma exec (seção 1).

## 6. Conexões com o banco

Cada réplica da API e o migrate abrem no máximo 10 conexões (pool 5 + 5 extras); o worker, no máximo 2. Com 2 réplicas da API, worker e migrate, são cerca de 32 no pior caso, dentro do padrão de 100 do Postgres.

## 7. Logs

Tudo em stdout, uma linha por evento, com timestamp (`2026-11-25T14:03:12-0300 INFO [composicao] ...`). Não há arquivo de log. Para as evidências, use `docker compose logs api worker`. Com o banco fora, cada requisição gera **uma** linha `WARNING ... banco indisponível ... 503`, sem traceback.

Os logs de acesso do uvicorn saem no mesmo formato (`... INFO [uvicorn.access] 172.18.0.5:41234 "POST /leituras HTTP/1.1" 201 Created`), com o IP real do cliente quando o Nginx repassa o `X-Forwarded-For`.

O worker escreve uma linha por ciclo que processou alguma leitura (ocioso, não escreve nada). Essas linhas são a evidência do teste de falha do worker:

```
2026-11-25T11:46:11-0400 INFO [worker] ciclo: processadas=500 alertas=1 backlog=700 duracao_ms=47
2026-11-25T11:46:13-0400 WARNING [worker] ciclo: banco indisponível (OperationalError); nova tentativa em 2s
2026-11-25T11:46:17-0400 INFO [worker] ciclo: banco disponível novamente
2026-11-25T11:46:23-0400 INFO [worker] SIGTERM recebido; encerrando depois do ciclo atual
```

Para o teste de falha do Postgres, o intervalo entre o último `banco indisponível` e o `banco disponível novamente` é o tempo de recuperação do worker.

O simulador escreve um resumo a cada 10 s, com totais acumulados, e uma linha na queda e na volta da API. São a evidência dos testes de falha da API:

```
2026-11-25T15:58:44+0000 INFO [simulador] resumo: enviadas=100 novas=100 duplicadas=0 rejeitadas=0 falhas=0 perdidas=0 buffer=0
2026-11-25T15:58:56+0000 WARNING [simulador] API indisponível (URLError: ...); leituras ficam no buffer
2026-11-25T15:59:02+0000 INFO [simulador] API disponível novamente; reenviando o buffer
2026-11-25T15:59:16+0000 INFO [simulador] resumo final: enviadas=275 novas=275 duplicadas=0 rejeitadas=0 falhas=19 perdidas=0 buffer=0
```

- `buffer` cresce enquanto a API (ou o banco) está fora e volta a 0 depois. O buffer tem até 1000 leituras por sensor (cerca de 33 min com `INTERVALO=2`); além disso, as mais antigas são descartadas e contadas em `perdidas`.
- `duplicadas` conta reenvios que a API já tinha gravado (resposta `200`): é a idempotência funcionando.
- O buffer fica em memória: um `docker stop` no **simulador** perde o que estava nele, e o resumo final avisa quantas leituras eram.
- No teste "parar as duas réplicas", compare `enviadas` (novas + duplicadas) com `SELECT count(*), count(DISTINCT id) FROM leituras` (seção 9).

## 8. Smoke test do deploy

```bash
BASE=https://dominio
curl -fsS $BASE/health                                    # 200
curl -sS -o /dev/null -w "%{http_code}\n" $BASE/pacientes   # 401 (protegido)
curl -fsS -X POST $BASE/auth/login -H "Content-Type: application/json" \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"senha\":\"$ADMIN_PASSWORD\"}"   # 200 com access_token
```

## 9. Consultas para os testes de falha

```sql
-- leituras duplicadas? (os dois números precisam ser iguais)
SELECT count(*), count(DISTINCT id) FROM leituras;

-- backlog do worker
SELECT count(*) FROM leituras WHERE processada = false;

-- latência ponta a ponta (sensor → banco)
SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY recebido_em - ts) FROM leituras;

-- alertas abertos por tipo
SELECT tipo, count(*) FROM alertas WHERE status = 'aberto' GROUP BY tipo;
```

## 10. CI (GitHub Actions)

Testes do backend, dentro de `backend/`:

```bash
pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql+psycopg://monitor:teste@localhost:5432/monitor_teste pytest -q
```

Use um serviço `postgres:16` no job do backend (usuário `monitor`, senha `teste`, banco `monitor_teste`). Os testes **apagam as tabelas** do banco apontado: nunca use o banco de produção. Sem `TEST_DATABASE_URL`, os testes de integração são pulados (não falham), então o CI precisa defini-la para cobrir tudo.

Testes do simulador, dentro de `simulador/` (sem banco nem rede):

```bash
pip install -r requirements-dev.txt
pytest -q
```

Mudanças neste contrato (porta, variável, comando ou healthcheck) serão avisadas antes, e este arquivo será atualizado no mesmo PR.
