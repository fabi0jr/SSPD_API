# Protocolo de integração: dashboard ↔ API

Contrato entre o dashboard (front) e a API do backend. Estado em 25/11/2026, etapa 5 do backend.

**Fonte da verdade:** o Swagger em `/docs` e o contrato OpenAPI em `/openapi.json`. Se este texto e o Swagger divergirem, vale o Swagger, e o backend precisa corrigir este arquivo. Os tipos TypeScript podem ser gerados do contrato, por exemplo com `npx openapi-typescript http://localhost:8000/openapi.json -o src/api/tipos.ts`.

> Todos os pacientes são **fictícios** (LGPD: dados de saúde são dados pessoais sensíveis). O dashboard não deve permitir cadastrar dados reais.

## 1. Endereço da API

| Ambiente | URL base |
|---|---|
| Local | `http://localhost:8000` (subir conforme o [README](../README.md#como-rodar-localmente)) |
| VPS | o domínio HTTPS do Nginx (definido pela infra) |

Deixe a URL base configurável (ex.: `VITE_API_URL`), sem fixar no código.

**CORS:** a origem do dashboard (ex.: `http://localhost:5173` ou `https://dominio-do-dashboard`) precisa estar em `CORS_ORIGINS` na API. Se o Nginx servir o dashboard e a API no mesmo domínio, o CORS nem entra em jogo. Métodos liberados: `GET`, `POST` e `PATCH`. Headers liberados: `Authorization` e `Content-Type`. O header `Retry-After` é exposto ao JavaScript. Todas as respostas, inclusive 401, 503 e 500, levam os headers de CORS, então o front sempre consegue ler o status e o corpo.

## 2. Autenticação

1. `POST /auth/login` com `{"email": "...", "senha": "..."}`.
2. A resposta traz `{"access_token": "<jwt>", "token_type": "bearer"}`.
3. Todas as outras rotas do dashboard levam o header `Authorization: Bearer <access_token>`.
4. O token vale `JWT_EXP_MINUTES` minutos (padrão 60). **Não existe refresh token:** quando qualquer rota responder **401**, descarte o token e mande o usuário para o login.

O usuário é criado pelo seed (`ADMIN_EMAIL` / `ADMIN_PASSWORD`). Não há cadastro de usuário, logout no servidor nem perfis: o "logout" é só apagar o token no front.

Guarde o token em memória ou em `sessionStorage`, nunca em cookie sem `HttpOnly` nem em log. Não decodifique o token para tomar decisões: o front só precisa repassá-lo.

```js
const r = await fetch(`${API}/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, senha }),
});
if (r.status === 401) mostrarErro("E-mail ou senha inválidos");
const { access_token } = await r.json();
```

## 3. Rotas

Todas respondem JSON. Timestamps vêm sempre em **ISO 8601 UTC com `Z`** (ex.: `2026-11-25T14:03:12.481Z`); converta para o fuso local só na exibição.

### `GET /pacientes`: tela principal

Lista ordenada por `id`. Cada item:

```json
{
  "id": "P001",
  "nome": "Paciente Fictício 001",
  "leito": "L-001",
  "criado_em": "2026-11-25T13:00:00Z",
  "ultima_leitura": {
    "id": "6f1c2e7a-3b2d-4c4e-9a51-0b8f3a1d2c77",
    "paciente_id": "P001",
    "fc": 82,
    "spo2": 97.4,
    "temp": 36.7,
    "ts": "2026-11-25T14:03:12.481Z",
    "recebido_em": "2026-11-25T14:03:12.530Z",
    "processada": true
  },
  "alertas_abertos": 1
}
```

- `ultima_leitura` é `null` quando o paciente ainda não mandou leitura.
- `leito` pode ser `null`.
- `processada: false` quer dizer que o worker de alertas ainda não avaliou a leitura.

### `POST /pacientes`: cadastro

Corpo `{"id": "P051", "nome": "Paciente Fictício 051", "leito": "L-051"}`. `id` com 1 a 10 caracteres, `nome` com 1 a 100 (não pode ser só espaços) e `leito` opcional, com até 20.

| Status | Significado |
|---|---|
| 201 | criado; devolve o paciente com `criado_em` |
| 409 | já existe paciente com esse `id` |
| 422 | campo inválido; ver o formato na seção 4 |

### `GET /pacientes/{id}/ultima`

Devolve uma leitura (mesmo formato de `ultima_leitura`) ou `null` se ainda não houver leituras. Responde 404 se o paciente não existir.

### `GET /pacientes/{id}/leituras`: histórico e gráficos

Parâmetros opcionais:

| Parâmetro | Formato | Padrão |
|---|---|---|
| `desde` | ISO 8601 **com fuso** (`2026-11-25T13:00:00Z`) | sem limite |
| `ate` | ISO 8601 **com fuso** | sem limite |
| `limit` | inteiro de 1 a 1000 | 100 |

A lista vem por `ts` **decrescente** (mais recente primeiro): para desenhar um gráfico da esquerda para a direita, inverta. `desde` e `ate` são inclusivos. Data sem fuso responde 422 (use `new Date(...).toISOString()`). Paciente inexistente responde 404.

### `GET /alertas`: painel de alertas

Parâmetros opcionais: `status` (`aberto` ou `reconhecido`; sem ele, vêm os dois), `paciente_id` e `limit` (1 a 1000, padrão 100). A lista vem por `criado_em` decrescente.

```json
{
  "id": 42,
  "paciente_id": "P001",
  "leitura_id": "6f1c2e7a-3b2d-4c4e-9a51-0b8f3a1d2c77",
  "tipo": "SPO2_BAIXA",
  "valor": 87.5,
  "severidade": "ALTA",
  "status": "aberto",
  "criado_em": "2026-11-25T14:03:14.002Z"
}
```

Valores possíveis, com sugestão de rótulo:

| `tipo` | Condição | `severidade` | Rótulo sugerido |
|---|---|---|---|
| `SPO2_BAIXA` | SpO₂ < 90 % | `ALTA` | Saturação baixa |
| `TAQUICARDIA` | FC > 120 bpm | `MEDIA` | Taquicardia |
| `BRADICARDIA` | FC < 50 bpm | `MEDIA` | Bradicardia |
| `FEBRE` | temperatura ≥ 38,0 °C | `MEDIA` | Febre |
| `HIPOTERMIA` | temperatura < 35,0 °C | `ALTA` | Hipotermia |

`valor` é o sinal que disparou o alerta, na unidade do tipo (%, bpm ou °C).

Há **no máximo um alerta aberto por paciente e tipo**. Enquanto ele estiver aberto, novas leituras alteradas do mesmo tipo não geram outro.

### `PATCH /alertas/{id}`: reconhecer

Corpo `{"status": "reconhecido"}` (é o único valor aceito). Devolve o alerta atualizado.

| Status | Significado |
|---|---|
| 200 | reconhecido; repetir também responde 200 (pode reenviar sem medo) |
| 404 | alerta inexistente |
| 422 | status diferente de `reconhecido` |

Depois do reconhecimento, se o paciente continuar alterado, o worker cria um alerta **novo** do mesmo tipo.

## 4. Erros

O corpo de erro é sempre `{"detail": ...}`.

| Status | `detail` | O que o front faz |
|---|---|---|
| 401 | `"token inválido ou ausente"` (rotas) ou `"e-mail ou senha inválidos"` (login) | volta para o login |
| 404 | texto, ex. `"paciente 'P999' não encontrado"` | mostra "não encontrado" |
| 409 | texto, ex. `"paciente 'P001' já existe"` | mostra a mensagem no formulário |
| 422 | lista: `[{"loc": ["body", "nome"], "msg": "...", "type": "..."}]` | marca o campo `loc[loc.length - 1]` com a `msg` |
| 503 | `"banco de dados indisponível, tente novamente"`, com header `Retry-After: 5` | mostra um aviso "sistema instável" e tenta de novo depois do `Retry-After` |
| 500 | `"erro interno"` | mensagem genérica |

O 422 também pode vir de parâmetro de URL (`loc: ["query", "limit"]`).

## 5. Atualização da tela

Não há WebSocket nem SSE: o dashboard faz **polling**.

| Dado | Rota | Intervalo sugerido |
|---|---|---|
| Alertas abertos | `GET /alertas?status=aberto` | 5 s |
| Visão geral | `GET /pacientes` | 5 a 10 s |
| Gráfico do paciente aberto | `GET /pacientes/{id}/leituras?desde=<última ts recebida>` | 5 s |

O worker avalia as leituras a cada `WORKER_INTERVALO` segundos (padrão 2), então um alerta aparece poucos segundos depois da leitura. Pare o polling quando a aba estiver oculta (`document.visibilityState`). Em caso de 503, espere o `Retry-After` antes de tentar de novo.

## 6. Estado atual e testes locais

- Todas as rotas acima estão prontas (backend, etapa 5).
- **O processo do worker ainda não existe** (etapa 6): até lá, leituras chegam com `processada: false` e nenhum alerta é criado sozinho. Para testar a tela de alertas antes disso, insira um alerta à mão no banco local:

  ```sql
  INSERT INTO alertas (paciente_id, leitura_id, tipo, valor, severidade)
  SELECT paciente_id, id, 'SPO2_BAIXA', spo2, 'ALTA' FROM leituras LIMIT 1;
  ```

- O simulador de sensores também é da etapa 7. Para ter leituras, use o `curl` do [README](../README.md#5-testar).

Mudanças no contrato serão avisadas antes, e este arquivo será atualizado no mesmo PR.
