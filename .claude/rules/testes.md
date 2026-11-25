---
paths:
  - "backend/tests/**"
  - "backend/pyproject.toml"
  - "backend/requirements-dev.txt"
  - "tests-carga/**"
---

# Testes

Comandos para rodar: seção "Comandos" do `CLAUDE.md`. Os casos de `ReconhecerAlerta`, login e rotas de consulta são da etapa 5; o workflow do GitHub Actions ainda não existe.

## Unitários (`tests/unit`), sem banco
Usam os fakes em memória de `tests/fakes.py`, que implementam as portas. Rodam em milissegundos.
- `regras.avaliar` com casos de borda (spo2 = 90 não alerta, 89.9 alerta etc.)
- Validação de faixa das entidades
- `RegistrarLeitura`: leitura nova → True; mesmo `id` → False; paciente inexistente → `PacienteNaoEncontrado`; `ts` mais de 5 min no futuro (relógio fake) → `LeituraInvalida`
- `ProcessarPendentes`: pendentes → alertas criados e leituras marcadas; anti-spam contra alertas abertos no banco **e** dentro do mesmo lote; exceção no meio → nada marcado (o fake de `FilaPendentes` simula o rollback); `backlog` preenchido
- `ReconhecerAlerta`: alerta inexistente → erro de domínio; alerta já reconhecido → sem erro
- Regra de dependência: um teste lê os imports de `dominio/` e `aplicacao/` (via `ast`) e falha se houver import proibido

Os fakes também precisam cumprir o contrato das portas (o fake da fila descarta as mudanças se houver exceção dentro do `with`).

## Integração (`tests/integracao`), com Postgres real, rodando no GitHub Actions
- Adaptadores Postgres: `salvar_se_nova` duas vezes com o mesmo id → uma linha só; `reservar` com dois workers concorrentes → nenhuma leitura processada duas vezes; exceção dentro do `with` → rollback
- HTTP (`TestClient`): `POST /leituras` válida → 201; reenvio → 200; API key errada → 401; fora da faixa → 422; paciente inexistente → 404
- Login correto → token; login errado → 401; rota protegida sem token → 401
- `GET /pacientes/{id}/leituras` respeita `limit` e a ordenação
- `/health` → 200 com o banco no ar

O banco de teste é um Postgres real (serviço `postgres` no workflow do Actions ou via Compose local). Não usar SQLite, porque `ON CONFLICT`, `SKIP LOCKED` e `TIMESTAMPTZ` têm comportamento diferente.

## Convenções dos testes de integração

- O esquema dos testes é criado pela própria migration (`alembic downgrade base` + `upgrade head`), não por `create_all`, para testar a migration.
- Os testes de integração são marcados `integracao` automaticamente pelo `conftest.py`.
- O teste de reservas simultâneas usa `lock_timeout` para falhar em 2 s, em vez de pendurar o CI, se alguém tirar o `SKIP LOCKED`.
- Os testes foram conferidos quebrando o código de propósito (anti-spam, marcação, `SKIP LOCKED`, `ON CONFLICT`): em todos os casos algum teste falha.

## Desempenho (script k6 em `/tests-carga`)
- Cenários com 10, 100 e 500 sensores virtuais fazendo `POST /leituras`
- Métricas: latência p50/p95/p99, requisições por segundo, taxa de erro
- CPU/RAM da VPS coletados pelo amigo da infra (`docker stats`)
- Exportar o resumo em JSON para gerar a tabela e o gráfico do relatório
