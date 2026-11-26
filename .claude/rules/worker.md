---
paths:
  - "backend/app/adaptadores/entrada/worker/**"
  - "backend/app/aplicacao/processar_pendentes.py"
  - "backend/app/dominio/regras.py"
  - "backend/app/adaptadores/saida/postgres/fila_pendentes.py"
  - "backend/tests/unit/test_processar_pendentes.py"
  - "backend/tests/unit/test_regras.py"
  - "backend/tests/integracao/test_fila_pendentes_pg.py"
  - "backend/tests/unit/test_worker.py"
  - "backend/tests/integracao/test_worker_pg.py"
---

# Worker de alertas

Tudo implementado: caso de uso `ProcessarPendentes`, regras, fila no Postgres e o adaptador de entrada `worker/` (etapa 6). Comando: `python -m app.composicao worker` (`composicao.rodar_worker`).

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

Logar em cada ciclo **que processou alguma leitura** (ciclo vazio não loga, decisão do Gerson): quantidade processada, quantidade de alertas criados e tamanho do backlog (`ResultadoCiclo.backlog`, obtido com `fila.contar_pendentes()` depois do commit). Esse log é a evidência do teste de falha do worker.

Encerrar de forma limpa ao receber SIGTERM (`docker stop`).

Como foi implementado (`adaptadores/entrada/worker/main.py`, classe `Worker`):
- Espera com `parar.wait(intervalo)` (um `threading.Event`), não `sleep`: o SIGTERM acorda o loop na hora. O handler de sinal só marca o evento, então o ciclo em andamento chega ao commit.
- Lote cheio (`processadas >= WORKER_LOTE`) com `backlog > 0`: o próximo ciclo começa sem esperar, para esvaziar rápido o backlog depois de uma queda.
- Exceções de "banco fora" chegam do `composicao.py` (`ERROS_INDISPONIBILIDADE`) e geram uma linha de WARNING sem traceback; as demais, `log.exception`. Nos dois casos o loop continua. O primeiro ciclo bem-sucedido depois de uma queda loga `ciclo: banco disponível novamente`, mesmo sem pendentes (é a evidência do tempo de recuperação).
- O worker **não** chama `aguardar_banco`: o loop já tenta de novo a cada ciclo, sem prazo, e uma espera longa no início atrasaria a resposta ao SIGTERM. Pool de 2 conexões.
- `WORKER_INTERVALO` e `WORKER_LOTE` são inteiros maiores que zero; fora disso, `ConfiguracaoAusente` no start.
- Testes: `tests/unit/test_worker.py` (loop com caso de uso roteirizado e `EventoFake`) e `tests/integracao/test_worker_pg.py` (montado pelo `composicao.py`).
