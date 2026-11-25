---
paths:
  - "backend/app/adaptadores/entrada/http/**/*.py"
  - "backend/app/composicao.py"
  - "backend/app/dominio/erros.py"
  - "backend/tests/unit/test_http.py"
  - "backend/tests/integracao/test_http_pg.py"
---

# Contrato da API

Implementados hoje: `GET /health` e `POST /leituras`. As demais rotas e o JWT são da etapa 5.

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
