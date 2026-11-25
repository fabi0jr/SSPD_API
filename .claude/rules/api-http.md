---
paths:
  - "backend/app/adaptadores/entrada/http/**/*.py"
  - "backend/app/composicao.py"
  - "backend/app/dominio/erros.py"
  - "backend/tests/unit/test_http.py"
  - "backend/tests/integracao/test_http_pg.py"
---

# Contrato da API

Todas as rotas abaixo estão implementadas (etapa 5). Detalhes de cada resposta: `docs/backend.md`, seção 7.

Respostas em JSON. Erros no formato padrão do FastAPI (`{"detail": ...}`). Timestamps em ISO 8601 UTC.

## Autenticação
- **Sensores:** header `X-API-Key: <SENSOR_API_KEY>`. Só vale para `POST /leituras`.
- **Dashboard:** `Authorization: Bearer <JWT>`, obtido em `POST /auth/login`.

## Endpoints

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

## Rotas do dashboard: decisões

- JWT HS256 com `sub` (e-mail), `iat` e `exp`. `algorithms=["HS256"]` fixo no decode. Token ausente, inválido ou expirado → 401 `{"detail":"token inválido ou ausente"}` com `WWW-Authenticate: Bearer`, conferido antes do corpo (como a API key).
- Login errado (e-mail inexistente ou senha errada) → 401 `{"detail":"e-mail ou senha inválidos"}`, sem dizer qual. E-mail inexistente também roda o bcrypt (hash fictício).
- `GET /pacientes/{id}/ultima`: 404 se o paciente não existe; 200 com `null` se existe mas não tem leituras.
- `GET /pacientes/{id}/leituras`: 404 para paciente inexistente (não lista vazia). `desde`/`ate` precisam de fuso (sem fuso → 422) e são inclusivos.
- `GET /alertas`: sem `status`, traz todos. `paciente_id` inexistente → lista vazia.
- `limit` fora de 1–1000 → 422 (o caso de uso também corta em 1000).
- `PATCH /alertas/{id}` só aceita `reconhecido` (reabrir → 422).
- `POST /pacientes`: 201, 409 para id repetido, 422 no formato `loc: ["body", campo]`.
- Timestamps de saída sempre em UTC com `Z` (`DataHoraUtc` em `schemas.py`). A leitura de saída traz `recebido_em` e `processada`.

## `POST /leituras`

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
