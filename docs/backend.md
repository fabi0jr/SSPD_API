# Documentação técnica do backend

Descreve o que está implementado em 25/11/2026. O que ainda não existe aparece marcado como **pendente**. Para instalar e executar, veja o [README](../README.md). Os protocolos com as outras partes estão em [integracao-dashboard.md](integracao-dashboard.md) e [integracao-infra.md](integracao-infra.md).

## Sumário

1. [Escopo atual](#1-escopo-atual)
2. [Arquitetura](#2-arquitetura)
3. [Domínio](#3-domínio)
4. [Casos de uso](#4-casos-de-uso)
5. [Fila de leituras pendentes](#5-fila-de-leituras-pendentes)
6. [Modelo de dados](#6-modelo-de-dados)
7. [API HTTP](#7-api-http)
8. [Tolerância a falhas](#8-tolerância-a-falhas)
9. [Segurança da aplicação](#9-segurança-da-aplicação)
10. [Testes e resultados](#10-testes-e-resultados)
11. [Pendências](#11-pendências)

## 1. Escopo atual

Em execução, o sistema hoje tem a API, o worker de alertas e o PostgreSQL. A API valida e grava leituras, que ficam marcadas como pendentes (`processada = false`), e atende o dashboard: login com JWT, consulta de pacientes, histórico de leituras, lista de alertas e reconhecimento de alertas.

O worker é um processo separado da API. A cada ciclo ele reserva as leituras pendentes, aplica as regras de alerta e grava os alertas. Os serviços ainda rodam direto no Python: Dockerfile e Compose são as próximas etapas.

## 2. Arquitetura

O backend usa uma arquitetura hexagonal leve (portas e adaptadores). A API HTTP e o worker são adaptadores de entrada diferentes sobre o mesmo núcleo, e o PostgreSQL é um adaptador de saída.

```
   [FastAPI / HTTP] ──────────┐                         ┌── [Postgres: repositórios]
                              ├─> aplicacao ─> dominio <┤
   [Worker / polling]   ──────┘   (casos de uso) (portas)└── [Postgres: fila de pendentes]
   adaptadores de entrada                                   adaptadores de saída
```

### 2.1 Regra de dependência

| Camada | Pode importar | Não pode importar |
|---|---|---|
| `dominio` | biblioteca padrão | `aplicacao`, `adaptadores`, FastAPI, SQLAlchemy, Pydantic |
| `aplicacao` | `dominio` | `adaptadores`, FastAPI, SQLAlchemy, Pydantic |
| `adaptadores/entrada` | `aplicacao`, `dominio` | `adaptadores/saida`, `composicao` |
| `adaptadores/saida` | `dominio` | `aplicacao`, `adaptadores/entrada`, `composicao` |
| `composicao.py` | tudo | é o único ponto que liga as implementações concretas |

O teste [test_arquitetura.py](../backend/tests/unit/test_arquitetura.py) lê os imports de cada camada e falha quando a regra é quebrada. O [seed.py](../backend/app/seed.py) é um entrypoint de infraestrutura, como o `composicao.py`, e usa o adaptador PostgreSQL diretamente.

### 2.2 Portas

As portas são `typing.Protocol` definidos em [portas.py](../backend/app/dominio/portas.py). Cada uma tem uma implementação em PostgreSQL e uma em memória, em [fakes.py](../backend/tests/fakes.py), usada nos testes unitários.

| Porta | Responsabilidade | Usada por |
|---|---|---|
| `RepositorioPacientes` | consultar, listar e criar pacientes | API |
| `RepositorioLeituras` | gravar leituras e consultar histórico | API |
| `RepositorioAlertas` | listar, obter e atualizar alertas | API |
| `RepositorioUsuarios` | buscar usuário por e-mail | API (login) |
| `FilaPendentes` e `LotePendente` | reservar e processar leituras pendentes | worker |

### 2.3 Composição

A injeção de dependência é manual. A função `criar_app_http`, em [composicao.py](../backend/app/composicao.py), lê o ambiente, cria o engine, aguarda o banco e entrega ao adaptador HTTP um objeto `Componentes` com os casos de uso e a função de verificação do banco.

O adaptador HTTP não conhece o SQLAlchemy: as exceções que significam "banco fora" também são passadas pela composição.

Para expor um novo caso de uso em uma rota: criar um campo em `Componentes`, uma função em [deps.py](../backend/app/adaptadores/entrada/http/deps.py) e a montagem em `montar_componentes_http`.

O worker segue o mesmo padrão: `rodar_worker` lê o ambiente e `montar_worker` entrega ao adaptador [worker/main.py](../backend/app/adaptadores/entrada/worker/main.py) o `ProcessarPendentes` já ligado à fila do PostgreSQL e as exceções que significam "banco fora". O comando é `python -m app.composicao worker`.

## 3. Domínio

### 3.1 Entidades

Em [entidades.py](../backend/app/dominio/entidades.py): `Leitura`, `Alerta`, `Paciente`, `Usuario`, `ResumoPaciente` e `ResultadoCiclo`.

Uma entidade inválida não chega a ser criada. As faixas de `Leitura` (extremos incluídos) são:

| Campo | Faixa aceita |
|---|---|
| `fc` (bpm) | 20 a 250 |
| `spo2` (%) | 50 a 100 |
| `temp` (°C) | 30 a 43 |

Valores fora da faixa indicam sensor com defeito ou dado corrompido, por isso são rejeitados em vez de gerar alerta. O `ts` precisa ter fuso horário.

A regra "`ts` no máximo 5 minutos no futuro" depende do relógio e fica em `Leitura.validar_horario(agora)`. Uma leitura antiga, reenviada depois de uma falha, continua válida.

### 3.2 Regras de alerta

Funções puras em [regras.py](../backend/app/dominio/regras.py):

| Tipo | Condição | Severidade |
|---|---|---|
| `SPO2_BAIXA` | spo2 < 90 | ALTA |
| `TAQUICARDIA` | fc > 120 | MEDIA |
| `BRADICARDIA` | fc < 50 | MEDIA |
| `FEBRE` | temp ≥ 38,0 | MEDIA |
| `HIPOTERMIA` | temp < 35,0 | ALTA |

Uma mesma leitura pode gerar mais de um alerta. Os limites são uma simplificação para o trabalho acadêmico e não têm validação clínica.

### 3.3 Erros

| Erro de domínio | Status HTTP |
|---|---|
| `LeituraInvalida`, `PacienteInvalido` | 422 |
| `PacienteNaoEncontrado`, `AlertaNaoEncontrado` | 404 |
| `PacienteJaExiste` | 409 |
| `CredenciaisInvalidas` | 401 |

## 4. Casos de uso

### 4.1 `RegistrarLeitura`

[registrar_leitura.py](../backend/app/aplicacao/registrar_leitura.py):

1. valida o horário com o relógio recebido no construtor;
2. confirma que o paciente existe;
3. grava com `salvar_se_nova` e devolve `True` (nova) ou `False` (id repetido).

### 4.2 `ProcessarPendentes`

[processar_pendentes.py](../backend/app/aplicacao/processar_pendentes.py) executa um ciclo do worker:

1. reserva até `limite` leituras pendentes, das mais antigas para as mais novas;
2. aplica as regras a cada leitura;
3. aplica o anti-spam: não cria alerta se já houver um alerta `aberto` do mesmo tipo para o paciente, no banco ou no próprio lote;
4. grava os alertas e marca as leituras como processadas, na mesma transação;
5. depois do commit, devolve `ResultadoCiclo` (processadas, alertas criados e tamanho do backlog).

Se algo falhar no meio, a transação é desfeita e o lote é processado no ciclo seguinte.

### 4.3 `Autenticar`

[autenticar.py](../backend/app/aplicacao/autenticar.py) busca o usuário pelo e-mail e confere a senha com uma função recebida no construtor (o bcrypt fica no adaptador HTTP, em [seguranca.py](../backend/app/adaptadores/entrada/http/seguranca.py)). Se o e-mail não existir, a senha é conferida mesmo assim contra um hash fictício, para o tempo de resposta não revelar quais e-mails estão cadastrados. E-mail inexistente e senha errada levantam o mesmo `CredenciaisInvalidas`.

### 4.4 `CadastrarPaciente`

[cadastrar_paciente.py](../backend/app/aplicacao/cadastrar_paciente.py) grava o paciente (já validado pela entidade) e devolve o registro gravado. Id repetido levanta `PacienteJaExiste`.

### 4.5 `Consultas`

[consultas.py](../backend/app/aplicacao/consultas.py) reúne as leituras do dashboard: lista de pacientes com resumo, última leitura, histórico e alertas. Para paciente inexistente, a última leitura e o histórico levantam `PacienteNaoEncontrado` (404) em vez de devolver vazio. O limite é cortado em 1000.

### 4.6 `ReconhecerAlerta`

[reconhecer_alerta.py](../backend/app/aplicacao/reconhecer_alerta.py) muda o status do alerta para `reconhecido`. É idempotente: reconhecer de novo não grava outra vez nem dá erro. Alerta inexistente levanta `AlertaNaoEncontrado`. Como o índice único dos alertas só vale para `aberto`, depois do reconhecimento uma nova leitura alterada do mesmo tipo volta a gerar alerta.

## 5. Fila de leituras pendentes

Não há broker de mensagens. A fila é a tabela `leituras`, implementada em [fila_pendentes.py](../backend/app/adaptadores/saida/postgres/fila_pendentes.py):

```sql
SELECT * FROM leituras
 WHERE processada = false
 ORDER BY ts
 LIMIT :limite
   FOR UPDATE SKIP LOCKED
```

| Situação | Efeito |
|---|---|
| Ciclo termina sem erro | `COMMIT` |
| Exceção durante o ciclo | `ROLLBACK`, o lote volta a ficar pendente |
| Processo morre ou perde a conexão | o PostgreSQL desfaz a transação e solta as travas |
| Dois workers ao mesmo tempo | `SKIP LOCKED` faz cada um pegar leituras diferentes |

Dois workers ainda poderiam abrir o mesmo alerta a partir de leituras diferentes. O índice único parcial `uq_alertas_aberto_por_tipo` impede isso no banco, e a inserção repetida é ignorada. Os alertas são inseridos em ordem fixa (paciente, tipo) para evitar deadlock. Nessa disputa, o contador de um ciclo pode indicar um alerta a mais do que foi gravado.

### 5.1 Processo do worker

O adaptador de entrada [worker/main.py](../backend/app/adaptadores/entrada/worker/main.py) chama `ProcessarPendentes.executar(WORKER_LOTE)` a cada `WORKER_INTERVALO` segundos. Cada ciclo que processou alguma leitura gera uma linha no log; ciclos vazios não geram nada, para o log não encher com o worker ocioso:

```
2026-11-25T11:46:11-0400 INFO [worker] ciclo: processadas=500 alertas=1 backlog=700 duracao_ms=47
```

`backlog` é o número de leituras que continuam pendentes depois do commit. Essa linha é a evidência do teste de falha do worker.

| Situação | Comportamento |
|---|---|
| Lote cheio e ainda há pendentes | o ciclo seguinte começa sem esperar o intervalo, para esvaziar rápido o backlog acumulado durante uma queda |
| Banco fora | uma linha `WARNING ... banco indisponível (OperationalError)` por ciclo, sem traceback, e nova tentativa no ciclo seguinte, sem prazo. O worker não encerra. Na volta, uma linha `banco disponível novamente`, mesmo que não haja leituras a processar |
| Erro inesperado | traceback no log; o lote sofre rollback e é reprocessado no ciclo seguinte |
| `SIGTERM` (`docker stop`) ou `SIGINT` | o ciclo em andamento termina (a transação chega ao commit) e o processo sai com código 0, sem esperar o fim do intervalo |
| `WORKER_INTERVALO` ou `WORKER_LOTE` menor ou igual a zero, ou não inteiro | o worker não sobe (erro de configuração) |

Diferente da API, o worker não chama `aguardar_banco` no início: o próprio loop já tenta de novo a cada ciclo, e uma espera longa no início atrasaria a resposta ao `docker stop`. O pool do worker tem no máximo 2 conexões, porque ele usa uma sessão por vez.

## 6. Modelo de dados

Criado pela migration [0001_inicial.py](../backend/migrations/versions/0001_inicial.py).

**pacientes**

| Coluna | Tipo | Observação |
|---|---|---|
| `id` | VARCHAR(10) | chave primária, ex. `P001` |
| `nome` | VARCHAR(100) | fictício |
| `leito` | VARCHAR(20) | opcional |
| `criado_em` | TIMESTAMPTZ | padrão `now()` |

**leituras**

| Coluna | Tipo | Observação |
|---|---|---|
| `id` | UUID | chave primária, gerada pelo sensor |
| `paciente_id` | VARCHAR(10) | referencia `pacientes` |
| `fc` | SMALLINT | bpm |
| `spo2` | NUMERIC(4,1) | % |
| `temp` | NUMERIC(3,1) | °C |
| `ts` | TIMESTAMPTZ | hora da medição (sensor) |
| `recebido_em` | TIMESTAMPTZ | hora da chegada (servidor) |
| `processada` | BOOLEAN | padrão `false` |

**alertas**

| Coluna | Tipo | Observação |
|---|---|---|
| `id` | BIGSERIAL | chave primária |
| `paciente_id` | VARCHAR(10) | referencia `pacientes` |
| `leitura_id` | UUID | referencia `leituras` |
| `tipo` | VARCHAR(30) | ver seção 3.2 |
| `valor` | NUMERIC(5,1) | valor que disparou o alerta |
| `severidade` | VARCHAR(10) | `ALTA` ou `MEDIA` |
| `status` | VARCHAR(15) | `aberto` (padrão) ou `reconhecido` |
| `criado_em` | TIMESTAMPTZ | padrão `now()` |

**usuarios**

| Coluna | Tipo | Observação |
|---|---|---|
| `id` | SERIAL | chave primária |
| `email` | VARCHAR(120) | único |
| `senha_hash` | VARCHAR(200) | bcrypt |

**Índices**

| Índice | Colunas | Finalidade |
|---|---|---|
| `ix_leituras_paciente_ts` | `(paciente_id, ts DESC)` | histórico e última leitura |
| `ix_leituras_pendentes` | `(ts)` onde `processada = false` | fila do worker |
| `ix_alertas_status_criado_em` | `(status, criado_em DESC)` | listagem de alertas |
| `uq_alertas_aberto_por_tipo` | `(paciente_id, tipo)` único, onde `status = 'aberto'` | anti-spam |

Observações:

- `spo2` e `temp` são gravados com uma casa decimal (97,43 vira 97,4). A fila lê o valor já arredondado.
- `recebido_em - ts` mede o tempo entre a medição e a chegada. O valor inclui o tempo em buffer e a diferença de relógio do sensor.
- Qualquer mudança em [models.py](../backend/app/adaptadores/saida/postgres/models.py) exige uma migration nova. Há um teste para isso.

### 6.1 Seed

`python -m app.seed` cria o usuário administrador e os pacientes `P001` até `SEED_PACIENTES`. Pode ser executado várias vezes: registros existentes não são alterados. Mudar `ADMIN_PASSWORD` depois não troca a senha de um administrador já criado.

## 7. API HTTP

Respostas em JSON. Swagger em `/docs`, ReDoc em `/redoc` e contrato em `/openapi.json`.

### 7.1 `GET /health`

Executa `SELECT 1` no banco. Não exige autenticação.

| Status | Corpo |
|---|---|
| 200 | `{"status":"ok","db":"ok"}` |
| 503 | `{"status":"degradado","db":"indisponivel"}` |

### 7.2 `POST /leituras`

Exige o header `X-API-Key`.

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

Sucesso: `{"id": "<uuid>", "nova": true}` com 201, ou `"nova": false` com 200 quando o `id` já existia. O cliente deve tratar qualquer 2xx como sucesso.

| Status | Quando | Corpo |
|---|---|---|
| 201 | leitura nova | `{"id": ..., "nova": true}` |
| 200 | `id` repetido (reenvio) | `{"id": ..., "nova": false}` |
| 401 | chave ausente ou errada | `{"detail":"API key inválida ou ausente"}` |
| 404 | paciente não cadastrado | `{"detail":"paciente 'P999' não encontrado"}` |
| 422 | valor fora da faixa, `ts` no futuro ou formato inválido | `{"detail":[{"loc":["body","fc"],"msg":"...","type":"value_error"}]}` |
| 503 | banco indisponível | `{"detail":"banco de dados indisponível, tente novamente"}`, com `Retry-After: 5` |
| 500 | erro não previsto | `{"detail":"erro interno"}` |

Notas:

- A chave é conferida antes da validação dos campos: sem chave, um corpo com campo inválido responde 401. Um JSON malformado responde 422 mesmo sem chave.
- Nos 422 de formato (gerados pelo próprio FastAPI), o campo `type` varia conforme o erro.

### 7.3 `POST /auth/login`

Corpo `{"email": "...", "senha": "..."}`. Responde 200 com `{"access_token": "<jwt>", "token_type": "bearer"}`.

| Status | Quando | Corpo |
|---|---|---|
| 200 | credenciais corretas | token |
| 401 | e-mail inexistente ou senha errada | `{"detail":"e-mail ou senha inválidos"}` |
| 422 | campo faltando ou vazio | formato padrão do FastAPI |

O token é um JWT HS256 assinado com `JWT_SECRET`, com `sub` (e-mail), `iat` e `exp` (`JWT_EXP_MINUTES`, padrão 60). A API não guarda sessão: qualquer réplica valida o token sozinha.

### 7.4 Rotas do dashboard

Todas exigem `Authorization: Bearer <access_token>`. Sem token, com token expirado, com assinatura errada, com `alg: none` ou com a API key dos sensores no lugar, respondem 401 `{"detail":"token inválido ou ausente"}` com `WWW-Authenticate: Bearer`. Como na API key, o token é conferido antes da validação do corpo.

| Método | Rota | Respostas |
|---|---|---|
| GET | `/pacientes` | 200: lista ordenada por id, cada item com `ultima_leitura` (ou `null`) e `alertas_abertos` |
| POST | `/pacientes` | 201: paciente criado; 409: id repetido; 422: campo vazio ou longo demais |
| GET | `/pacientes/{id}/ultima` | 200: leitura, ou `null` se o paciente ainda não tem leituras; 404: paciente inexistente |
| GET | `/pacientes/{id}/leituras` | 200: leituras por `ts` decrescente; 404: paciente inexistente; 422: parâmetro inválido |
| GET | `/alertas` | 200: alertas por `criado_em` decrescente; 422: `status` ou `limit` inválido |
| PATCH | `/alertas/{id}` | 200: alerta reconhecido (também na repetição); 404: alerta inexistente; 422: status diferente de `reconhecido` |

Parâmetros:

- `GET /pacientes/{id}/leituras`: `desde` e `ate` (ISO 8601 **com** fuso, inclusivos) e `limit` (padrão 100, de 1 a 1000).
- `GET /alertas`: `status` (`aberto` ou `reconhecido`; sem ele, vêm os dois), `paciente_id` e `limit` (padrão 100, de 1 a 1000).
- `PATCH /alertas/{id}`: corpo `{"status": "reconhecido"}`. Reabrir não é permitido.

Os timestamps de saída (`ts`, `recebido_em`, `criado_em`) vêm sempre em UTC com `Z`, qualquer que seja o fuso enviado pelo sensor. A leitura de saída inclui `recebido_em` (a diferença para `ts` é a latência ponta a ponta) e `processada`. Os 503 e 500 seguem o mesmo formato do `POST /leituras`.

## 8. Tolerância a falhas

### 8.1 Mecanismos

| Mecanismo | Situação |
|---|---|
| Idempotência no `POST /leituras` (UUID do sensor e `ON CONFLICT DO NOTHING`) | ✅ implementado |
| Nova tentativa de conexão com o banco no início, com espera crescente (0,5 s até 5 s, por cerca de 30 s) | ✅ API, migrations e seed. O worker tenta de novo a cada ciclo, sem prazo |
| Modo degradado: a API sobe mesmo sem banco e o `/health` responde 503 | ✅ implementado |
| Recuperação sem reiniciar (`pool_pre_ping` descarta conexões mortas) | ✅ implementado |
| Falha rápida com banco fora (`connect_timeout` de 5 s, espera pelo pool de até 10 s) | ✅ implementado |
| Uma linha de log por falha esperada, sem traceback | ✅ implementado |
| Fechamento do pool de conexões no encerramento da API e do worker | ✅ implementado |
| Anti-spam com vários workers (`SKIP LOCKED` e índice único) | ✅ implementado no caso de uso e no adaptador |
| API sem sessão em memória, preparada para réplicas | ✅ no código; réplicas dependem do Compose e do Nginx (pendentes) |
| Migrations separadas da inicialização da API | ✅ comando separado; serviço `migrate` pendente |
| Processo do worker, desacoplado da ingestão | ✅ implementado (seção 5.1) |
| Encerramento limpo do worker por `SIGTERM`, sem perder o ciclo em andamento | ✅ implementado |
| Buffer local e reenvio no simulador | ⬜ pendente |

Cada réplica da API mantém no máximo 10 conexões (pool de 5, mais 5 extras); o worker, no máximo 2.

### 8.2 Cenários de falha previstos para o sistema completo

| Cenário | Comportamento esperado | Evidência a coletar |
|---|---|---|
| Parar uma réplica da API | a outra atende e o Nginx redistribui | logs do Nginx e do simulador |
| Parar as duas réplicas | o buffer do simulador cresce e esvazia na volta, sem duplicar | contagem de leituras antes e depois |
| Parar o worker | a ingestão continua e o backlog é processado na volta | log do worker e contagem de pendentes |
| Parar o PostgreSQL | API responde 503, simulador guarda as leituras, tudo se recupera | tempo até a recuperação |

Esses testes ainda **não foram executados** no ambiente completo. Os ensaios parciais já feitos estão na seção 10.3.

## 9. Segurança da aplicação

| Medida | Situação |
|---|---|
| API key para sensores, comparada em tempo constante | ✅ implementado |
| Validação de tipo e formato (Pydantic) e de faixas (domínio) | ✅ implementado |
| CORS restrito a `CORS_ORIGINS`; até as respostas 500 e 503 levam os headers de CORS, e o `Retry-After` é exposto | ✅ implementado |
| Segredos somente em variáveis de ambiente; `.env` no `.gitignore` | ✅ implementado |
| Respostas sem stack trace nem mensagem de exceção | ✅ implementado |
| Senha do administrador armazenada com bcrypt | ✅ implementado |
| Dados de pacientes fictícios (LGPD, art. 5º, II, e art. 11) | ✅ adotado |
| Login com JWT (HS256) para o dashboard; algoritmo fixo na validação (bloqueia `alg: none`); `exp` obrigatório | ✅ implementado |
| `JWT_SECRET` com no mínimo 32 caracteres, senão a API não sobe | ✅ implementado |
| Mesma resposta e tempo parecido para e-mail inexistente e senha errada | ✅ implementado |
| Container com usuário sem privilégios | ⬜ pendente (Dockerfile) |
| HTTPS, firewall, SSH e limite de requisições | ⬜ pendente (infraestrutura) |

## 10. Testes e resultados

### 10.1 Organização

| Conjunto | Banco | O que cobre |
|---|---|---|
| `tests/unit` (228 testes) | nenhum | regras e casos de borda, entidades, casos de uso, fakes, rotas HTTP (sensores e dashboard, incluindo tokens inválidos), loop do worker (logs, banco fora, erro inesperado, SIGTERM, configuração) e regra de dependência |
| `tests/integracao` (42 testes) | PostgreSQL 16 real | conexão, migration, repositórios, seed, fila com concorrência, rotas HTTP (login com o usuário do seed, histórico com `limit` e ordenação, reconhecimento de alertas) e worker montado pela composição (backlog em lotes, banco inacessível) |

Os testes de integração não usam SQLite, porque `ON CONFLICT`, `SKIP LOCKED` e `TIMESTAMPTZ` se comportam de forma diferente. O esquema é criado pela própria migration, o que também a testa.

### 10.2 Resultado

Execução de 25/11/2026, em WSL2 com PostgreSQL `16-alpine`:

| Execução | Resultado |
|---|---|
| Todos os testes | 270 passaram |
| Somente unitários | 228 passaram |
| Sem `TEST_DATABASE_URL` | 228 passaram e 42 foram pulados |

### 10.3 Ensaio manual da API

Execução de 25/11/2026 sobre uma cópia limpa do código, com **uma** instância da API e um PostgreSQL descartável:

| Passo | Resultado observado |
|---|---|
| Migration e seed com 10 pacientes | 1 usuário e 10 pacientes criados; a segunda execução criou 0 |
| `GET /health` | 200 |
| Leitura nova | 201 |
| Reenvio da mesma leitura | 200, `"nova": false` |
| Chave errada | 401 |
| Paciente inexistente | 404 |
| `fc` igual a 999 | 422 |
| PostgreSQL parado: `/health` e `POST /leituras` | 503, o segundo com `Retry-After: 5` |
| PostgreSQL religado | `/health` voltou a 200 sem reiniciar a API |
| Reenvio após a volta | 200, sem duplicar |
| Contagem final | 1 leitura gravada, pendente de processamento |

Ensaio das rotas do dashboard (25/11/2026, uvicorn com uma instância, banco limpo e seed com 3 pacientes):

| Passo | Resultado observado |
|---|---|
| Login com o admin do seed | 200 com token |
| Login com senha errada | 401 |
| `GET /alertas` sem token | 401 |
| `GET /pacientes` | 3 pacientes; P001 com a última leitura e 1 alerta aberto, os outros com `null` e 0 |
| `GET /alertas?status=aberto` | 1 alerta `SPO2_BAIXA` |
| `PATCH /alertas/1` | 200, `status: reconhecido` |

Ensaio do worker (25/11/2026, processo real `python -m app.composicao worker`, `WORKER_INTERVALO=1`, `WORKER_LOTE=500`, PostgreSQL descartável com 1200 leituras pendentes, duas delas alteradas):

| Passo | Resultado observado |
|---|---|
| Início com 1200 pendentes | 3 ciclos seguidos, sem espera: backlog 700, 200 e 0; 2 alertas criados |
| `docker stop` no PostgreSQL | uma linha `WARNING ... banco indisponível (OperationalError)` por segundo, sem traceback; o worker continuou no ar |
| `docker start` no PostgreSQL | `banco disponível novamente` cerca de 1 s depois, sem reiniciar |
| Sem leituras novas | nenhuma linha de ciclo no log |
| Leitura nova gravada após a volta | processada no ciclo seguinte |
| `kill -TERM` | `SIGTERM recebido; encerrando depois do ciclo atual` e `worker encerrado`; saiu com código 0 em 0,1 s |

### 10.4 Desempenho

⬜ Pendente. Previsto: k6 com 10, 100 e 500 sensores virtuais, medindo latência p50, p95 e p99, requisições por segundo e taxa de erro.

## 11. Pendências

Backend, na ordem prevista:

1. Dockerfile do backend e do simulador, e o simulador;
2. script de carga k6.

Comandos que o Docker Compose deverá usar, executados dentro de `backend/`:

| Serviço | Comando |
|---|---|
| `migrate` | `alembic upgrade head && python -m app.seed` |
| `api` | `uvicorn app.composicao:criar_app_http --factory --host 0.0.0.0 --port 8000 --proxy-headers` |
| `worker` | `python -m app.composicao worker` |

Pontos para a infraestrutura:

- A imagem `python:3.12-slim` não tem `curl`. O healthcheck pode usar `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"`.
- O Nginx precisa repassar `/docs` e `/openapi.json` para o Swagger funcionar.
- Os logs de acesso do Uvicorn saem sem data e hora; será preciso um `--log-config`.
- O CI precisa de um serviço `postgres:16` e da variável `TEST_DATABASE_URL`.
