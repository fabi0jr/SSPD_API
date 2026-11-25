---
paths:
  - ".env.example"
  - "backend/app/config.py"
  - "backend/app/composicao.py"
  - "backend/app/seed.py"
  - "backend/migrations/env.py"
  - "**/docker-compose*.yml"
  - "simulador/**"
---

# Variáveis de ambiente

Lidas pelo código hoje: `DATABASE_URL`, `SENSOR_API_KEY`, `JWT_SECRET`, `JWT_EXP_MINUTES`, `CORS_ORIGINS`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `SEED_PACIENTES`, `WORKER_INTERVALO`, `WORKER_LOTE` e `TEST_DATABASE_URL`, e, no simulador, `API_URL`, `SENSOR_API_KEY`, `N_PACIENTES` e `INTERVALO` (este aceita fração). O simulador lê o ambiente por conta própria (`ler_configuracao`), sem o `app/config.py`.

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
| `N_PACIENTES` / `INTERVALO` | simulador | `10` / `2` (`N_PACIENTES` ≤ `SEED_PACIENTES`) |
| `TEST_DATABASE_URL` | só testes de integração | `postgresql+psycopg://monitor:teste@localhost:55432/monitor_teste` |

Nunca commitar `.env`. Manter o `.env.example` atualizado sempre que surgir uma variável nova.

- Senha com caractere especial na `DATABASE_URL` precisa ir codificada (`@` → `%40`, `%` → `%25`). O `migrations/env.py` passa a URL direto ao SQLAlchemy, sem passar pelo `alembic.ini`, justamente para aceitar `%`.
- `TEST_DATABASE_URL` aponta para um banco **descartável**: os testes apagam as tabelas. Nunca apontar para produção. Sem ela, os testes de integração são pulados (não falham).
- Sem `SENSOR_API_KEY`, ou sem `JWT_SECRET` com pelo menos 32 caracteres, a API não sobe (erro de configuração). Sem `DATABASE_URL`, nenhum serviço sobe.
