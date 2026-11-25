---
paths:
  - "backend/app/composicao.py"
  - "backend/app/adaptadores/saida/postgres/db.py"
  - "backend/app/adaptadores/saida/postgres/fila_pendentes.py"
  - "backend/app/adaptadores/entrada/**"
  - "backend/migrations/env.py"
  - "simulador/**"
---

# Tolerância a falhas (parte do backend)

Os itens 2 (simulador) e a parte do worker nos itens 4 e 5 ainda não estão em execução.

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
