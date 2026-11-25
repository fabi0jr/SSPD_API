---
paths:
  - "backend/app/adaptadores/entrada/worker/**"
  - "backend/app/aplicacao/processar_pendentes.py"
  - "backend/app/dominio/regras.py"
  - "backend/app/adaptadores/saida/postgres/fila_pendentes.py"
  - "backend/tests/unit/test_processar_pendentes.py"
  - "backend/tests/unit/test_regras.py"
  - "backend/tests/integracao/test_fila_pendentes_pg.py"
---

# Worker de alertas

O caso de uso `ProcessarPendentes`, as regras e a fila no Postgres estão prontos. O adaptador de entrada `worker/` ainda não existe (etapa 6).

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
