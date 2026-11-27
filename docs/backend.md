# Documentação técnica do backend

Descreve o que está implementado em 26/11/2026. O que ainda não existe aparece marcado como **pendente**. Para instalar e executar, veja o [README](../README.md). Os protocolos com as outras partes estão em [integracao-dashboard.md](integracao-dashboard.md) e [integracao-infra.md](integracao-infra.md).

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
11. [Simulador e imagens Docker](#11-simulador-e-imagens-docker)
12. [Pendências](#12-pendências)

## 1. Escopo atual

Em execução, o sistema hoje tem a API, o worker de alertas, o PostgreSQL e o simulador de sensores. A API valida e grava leituras, que ficam marcadas como pendentes (`processada = false`), e atende o dashboard: cadastro de usuário, login com JWT, consulta de pacientes, histórico de leituras, lista de alertas e reconhecimento de alertas.

O worker é um processo separado da API. A cada ciclo ele reserva as leituras pendentes, aplica as regras de alerta e grava os alertas. O simulador gera leituras de pacientes fictícios e as envia para a API, guardando-as num buffer local enquanto a API estiver fora (seção 11). Backend e simulador têm imagem Docker, o sistema sobe com um `docker-compose.yml` com 2 réplicas da API atrás do Nginx (seção 11.3), e o `POST /leituras` tem um teste de carga com k6 (seção 10.4).

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
| `RepositorioUsuarios` | buscar usuário por e-mail e criar usuário (e-mail repetido é recusado) | API (login e cadastro) |
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
| `PacienteJaExiste`, `UsuarioJaExiste` | 409 |
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

### 4.4 `CadastrarUsuario`

[cadastrar_usuario.py](../backend/app/aplicacao/cadastrar_usuario.py) cria um usuário do dashboard a partir de e-mail e senha (`POST /auth/cadastro`). Como no `Autenticar`, o bcrypt fica fora do caso de uso: ele recebe no construtor a função que gera o hash e grava com `RepositorioUsuarios.criar(email, senha_hash)`, de modo que a senha em claro nunca chega ao repositório. Devolve o usuário criado (`id` e `email`), sem token: o login continua sendo do `Autenticar`. O formato do e-mail e o tamanho da senha são validados antes, na borda HTTP.

A unicidade do e-mail não é conferida com uma consulta antes do insert: quem garante é o índice `UNIQUE` de `usuarios.email`, que já existe desde a migration inicial. O adaptador PostgreSQL trata o conflito do insert e levanta `UsuarioJaExiste` (409, com a mensagem `e-mail já cadastrado`, que não repete o e-mail). Assim, duas réplicas cadastrando o mesmo e-mail ao mesmo tempo resultam em um 201 e um 409, e não em dois usuários.

O usuário criado é igual ao administrador do seed: não há perfis, então ele tem acesso a todas as rotas do dashboard (seção 9).

### 4.5 `CadastrarPaciente`

[cadastrar_paciente.py](../backend/app/aplicacao/cadastrar_paciente.py) grava o paciente (já validado pela entidade) e devolve o registro gravado. Id repetido levanta `PacienteJaExiste`.

### 4.6 `Consultas`

[consultas.py](../backend/app/aplicacao/consultas.py) reúne as leituras do dashboard: lista de pacientes com resumo, última leitura, histórico e alertas. Para paciente inexistente, a última leitura e o histórico levantam `PacienteNaoEncontrado` (404) em vez de devolver vazio. O limite é cortado em 1000.

### 4.7 `ReconhecerAlerta`

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

### 7.4 `POST /auth/cadastro`

Público: não exige token nem API key. Corpo `{"email": "fulano@exemplo.com", "senha": "senha-segura-123"}`. O e-mail tem os mesmos limites do login (3 a 120 caracteres), mais uma checagem simples de formato (`nome@dominio.tld`); não há normalização de maiúsculas nem de espaços, para o login funcionar com o mesmo texto digitado no cadastro. A senha tem de 8 caracteres a 72 bytes em UTF-8 (limite do bcrypt). Responde 201 com `{"id": 2, "email": "fulano@exemplo.com"}`, **sem token**: para entrar, o cliente chama `POST /auth/login` em seguida (o dashboard faz isso sozinho).

| Status | Quando | Corpo |
|---|---|---|
| 201 | usuário criado | `{"id": ..., "email": ...}` |
| 409 | e-mail já cadastrado | `{"detail":"e-mail já cadastrado"}` |
| 422 | campo faltando, e-mail fora do formato ou dos limites, senha com menos de 8 caracteres ou mais de 72 bytes | formato padrão do FastAPI (`loc: ["body", campo]`) |
| 503 | banco indisponível | igual ao `POST /leituras`, com `Retry-After: 5` |

A senha é gravada com bcrypt, como a do administrador. Ao contrário do `POST /leituras` e do `PATCH /alertas/{id}`, a rota **não é idempotente**: repetir o mesmo corpo responde 409.

### 7.5 Rotas do dashboard

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
| API sem sessão em memória, com 2 réplicas atrás do Nginx | ✅ implementado (seção 11.3) |
| Migrations separadas da inicialização da API | ✅ serviço `migrate` (one-shot), na mesma imagem |
| Processo do worker, desacoplado da ingestão | ✅ implementado (seção 5.1) |
| Encerramento limpo do worker por `SIGTERM`, sem perder o ciclo em andamento | ✅ implementado |
| Buffer local e reenvio no simulador, com o mesmo `id` (sem duplicar) | ✅ implementado (seção 11.1) |
| Encerramento limpo do simulador por `SIGTERM`, com resumo final no log | ✅ implementado |
| Retentativa na outra réplica pelo Nginx (rotas idempotentes), sem mascarar o 503 da API | ✅ implementado (configuração mínima do Nginx) |
| Reinício automático dos containers (`restart: unless-stopped`) e healthcheck da API no compose | ✅ implementado |

Cada réplica da API mantém no máximo 10 conexões (pool de 5, mais 5 extras); o worker, no máximo 2.

### 8.2 Cenários de falha previstos para o sistema completo

| Cenário | Comportamento esperado | Evidência a coletar |
|---|---|---|
| Parar uma réplica da API | a outra atende e o Nginx redistribui | logs do Nginx e do simulador |
| Parar as duas réplicas | o buffer do simulador cresce e esvazia na volta, sem duplicar | contagem de leituras antes e depois |
| Parar o worker | a ingestão continua e o backlog é processado na volta | log do worker e contagem de pendentes |
| Parar o PostgreSQL | API responde 503, simulador guarda as leituras, tudo se recupera | tempo até a recuperação |

Os quatro cenários foram ensaiados localmente com o `docker-compose.yml`, e todos se comportaram como esperado (seção 10.3). A execução oficial, com as evidências do relatório, é feita na VPS pela infraestrutura.

## 9. Segurança da aplicação

| Medida | Situação |
|---|---|
| API key para sensores, comparada em tempo constante | ✅ implementado |
| Validação de tipo e formato (Pydantic) e de faixas (domínio) | ✅ implementado |
| CORS restrito a `CORS_ORIGINS`; até as respostas 500 e 503 levam os headers de CORS, e o `Retry-After` é exposto | ✅ implementado |
| Segredos somente em variáveis de ambiente; `.env` no `.gitignore` | ✅ implementado |
| Respostas sem stack trace nem mensagem de exceção | ✅ implementado |
| Senhas dos usuários (administrador do seed e contas criadas no cadastro) armazenadas com bcrypt | ✅ implementado |
| Dados de pacientes fictícios (LGPD, art. 5º, II, e art. 11) | ✅ adotado |
| Login com JWT (HS256) para o dashboard; algoritmo fixo na validação (bloqueia `alg: none`); `exp` obrigatório | ✅ implementado |
| `JWT_SECRET` com no mínimo 32 caracteres, senão a API não sobe | ✅ implementado |
| Mesma resposta e tempo parecido para e-mail inexistente e senha errada | ✅ implementado |
| Container com usuário sem privilégios (`uid 10001`); o código pertence ao root e não pode ser alterado pelo processo | ✅ implementado (backend e simulador) |
| Imagem sem testes, venv nem `.env` (`.dockerignore`) | ✅ implementado |
| Banco e réplicas da API sem porta publicada; só o Nginx fica exposto | ✅ implementado (compose) |
| Cada container recebe só as variáveis de que precisa | ✅ implementado (compose) |
| HTTPS, firewall, SSH e limite de requisições | ⬜ pendente (infraestrutura) |
| Cadastro de usuário restrito (administrador, convite ou aprovação) e perfis de acesso | ⬜ fora do escopo: limitação descrita abaixo |

**Limitação consciente: cadastro público.** O `POST /auth/cadastro` é aberto, na tela de login, por decisão de projeto, para simplificar a demonstração. Como não há perfis nem controle de acesso por papel (fora do escopo do trabalho), qualquer pessoa que chegar à URL consegue criar uma conta e ver os dados dos pacientes. Isso é aceitável aqui porque todos os pacientes são fictícios. Outra consequência: o 409 do cadastro revela se um e-mail já tem conta, o que o login evita de propósito (resposta e tempo iguais para e-mail inexistente e senha errada). Num sistema com dados reais, seria preciso restringir o cadastro (feito só por um administrador, por convite ou com aprovação) e ter perfis de acesso; fica como trabalho futuro. Enquanto isso, o rate limit do Nginx sobre o cadastro limita a criação de contas em massa ([integracao-infra.md](integracao-infra.md), seção 4).

## 10. Testes e resultados

### 10.1 Organização

| Conjunto | Banco | O que cobre |
|---|---|---|
| `tests/unit` (249 testes) | nenhum | regras e casos de borda, entidades, casos de uso, fakes, rotas HTTP (sensores e dashboard, incluindo tokens inválidos e cadastro de usuário), loop do worker (logs, banco fora, erro inesperado, SIGTERM, configuração) e regra de dependência |
| `simulador/tests` (35 testes) | nenhum | geração das leituras (formato, UUID novo a cada leitura, faixas, anomalia de ~2%, perfis), classificação das respostas, buffer (ordem, reenvio com o mesmo `id`, descarte de rejeitadas, buffer cheio), detecção de queda e volta da API e configuração |
| `tests/integracao` (47 testes) | PostgreSQL 16 real | conexão, migration, repositórios (incluindo cadastro concorrente do mesmo e-mail), seed, fila com concorrência, rotas HTTP (login com o usuário do seed, cadastro seguido de login, histórico com `limit` e ordenação, reconhecimento de alertas) e worker montado pela composição (backlog em lotes, banco inacessível) |

Os testes de integração não usam SQLite, porque `ON CONFLICT`, `SKIP LOCKED` e `TIMESTAMPTZ` se comportam de forma diferente. O esquema é criado pela própria migration, o que também a testa.

### 10.2 Resultado

Execução de 26/11/2026, em WSL2 com PostgreSQL `16-alpine`:

| Execução | Resultado |
|---|---|
| Todos os testes | 270 passaram |
| Somente unitários | 228 passaram |
| Sem `TEST_DATABASE_URL` | 228 passaram e 42 foram pulados |
| Simulador | 35 passaram |

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

Ensaio ponta a ponta com as imagens Docker (25/11/2026, rede Docker local sem Nginx: PostgreSQL `16-alpine`, `migrate`, duas réplicas da API, worker e simulador com 10 sensores e `INTERVALO=1`, apontado para uma das réplicas):

| Passo | Resultado observado |
|---|---|
| `migrate` (`alembic upgrade head` e seed com 50 pacientes) | migration aplicada, 1 usuário e 50 pacientes; saiu com código 0 |
| Healthcheck sem `curl` (`python -c "import urllib.request..."`) | as duas réplicas `healthy` |
| Logs de acesso do uvicorn | com timestamp, em stdout: `... INFO [uvicorn.access] 127.0.0.1:55866 "GET /health HTTP/1.1" 200 OK` |
| Processos dos containers | `uid=10001(app)` |
| Simulador por 10 s | 100 leituras, todas `201` |
| `docker stop` na réplica usada pelo simulador | API encerrada em 1 s, com código 0; simulador: `API indisponível ...; leituras ficam no buffer` |
| `docker start` 15 s depois | `API disponível novamente; reenviando o buffer` no mesmo segundo; buffer zerado |
| `docker stop` no simulador | `SIGTERM recebido`, `resumo final: enviadas=275 novas=275 duplicadas=0 rejeitadas=0 falhas=19 perdidas=0 buffer=0`, em 0,3 s |
| Banco | `count(*) = count(DISTINCT id) = 275`: nada perdido nem duplicado |
| Alertas gerados pelo worker | 4 `SPO2_BAIXA` (anomalias) e 1 `FEBRE` (perfil febril) |
| `docker stop` no worker | `worker encerrado`, código 0, em 0,4 s |

Observação do ensaio: sem o Nginx, o simulador apontava direto para o nome do container. Com o container parado, o DNS do Docker repassa o nome para o DNS externo, que demorou cerca de 10 s para responder, e o `timeout` do envio não cobre a resolução de nome. Por isso a queda só apareceu no log do simulador cerca de 10 s depois do `docker stop`. Com o `API_URL` apontando para o Nginx, que continua no ar, a falha chega na hora como 502 ou 503.

Ensaio com o `docker-compose.yml` (26/11/2026, WSL2, `docker compose up -d --build --wait`, Nginx na porta 8080, simulador com 10 sensores e `INTERVALO=1`):

| Passo | Resultado observado |
|---|---|
| `up --wait` | tudo no ar em 18 s: `migrate` com código 0 (migration, 1 usuário e 50 pacientes), `api-1` e `api-2` `healthy` |
| `.env` sem as variáveis obrigatórias | o compose se recusa a subir e diz qual variável falta |
| Pelo Nginx | `/health` 200, `/docs` 200, `/pacientes` sem token 401, login 200, `GET /alertas` 200, `PATCH /alertas/{id}` 200, chave errada 401 |
| Balanceamento | em 20 s, 95 `POST /leituras` na `api-1` e 110 na `api-2` |
| Containers | API como `uid=10001(app)`; nenhuma porta publicada além da do Nginx |
| `docker stop` na `api-1` por 20 s | `/health` sempre 200; o simulador **não registrou nenhuma falha**: o Nginx tentou a `api-1`, recebeu conexão recusada e reenviou para a `api-2`. Na volta, a `api-1` recebeu tráfego de novo sem reiniciar o Nginx |
| `docker stop` nas duas réplicas por 20 s | `/health` 502; o simulador guardou as leituras no buffer (`API indisponível (HTTP 502)`) e esvaziou ao voltar; `count(*) = count(DISTINCT id) = 1335` |
| `docker stop` no worker por 20 s | ingestão normal; backlog de 23 para 223 e de volta a 20 (o fluxo normal) 5 s depois do `docker start` |
| `docker stop` no PostgreSQL | pelo Nginx, `/health` 503 `degradado` e `POST /leituras` 503 com `Retry-After: 5` (o 503 da API chega ao cliente); `/health` voltou a 200 2 s depois do `docker start`; worker: `banco indisponível` e `banco disponível novamente` |
| Contagem final | `count(*) = count(DISTINCT id) = 1920`; o simulador contou 8 `duplicadas`: reenvios de leituras que a API já tinha gravado, respondidos com 200 |

Ajustes feitos durante o ensaio: o `location /alertas/` do Nginx respondia 301 ao `GET /alertas` (ficou `location /alertas`), e montar só o arquivo `default.conf` fazia o container continuar com a versão antiga depois de uma edição (o compose monta a pasta `infra/nginx`).

### 10.4 Desempenho

Script: `tests-carga/leituras.js` (k6 2.3.0, rodando no Docker). Cada usuário virtual é um sensor que envia uma leitura válida a cada 2 s para o seu paciente, com a API key, como o simulador. O resumo de cada execução fica em JSON em `tests-carga/resultados/`. Como rodar: [tests-carga/README.md](../tests-carga/README.md).

**Ensaio local** (26/11/2026): WSL2 com Xeon W-1270 (16 threads), **um** processo uvicorn sem Nginx, PostgreSQL `16-alpine` no Docker, seed com 500 pacientes, worker parado e k6 na mesma máquina. Cada cenário durou 1 minuto, com `INTERVALO=2`:

| Sensores | Esperado (req/s) | Medido (req/s) | Requisições | p50 (ms) | p95 (ms) | p99 (ms) | Máx. (ms) | Erros |
|---|---|---|---|---|---|---|---|---|
| 10 | 5 | 4,8 | 300 | 8,1 | 10,2 | 11,0 | 13,8 | 0% |
| 100 | 50 | 48,4 | 3000 | 8,1 | 10,9 | 12,8 | 64,4 | 0% |
| 500 | 250 | 166,2 | 10305 | 2960 | 3296 | 3512 | 4149 | 0% |

Todas as respostas foram `201`. No banco, `count(*) = count(DISTINCT id)`: nenhuma leitura duplicada.

Leitura dos resultados:

- Com 10 e 100 sensores, a API atende toda a carga, com p95 em torno de 11 ms.
- Com 500 sensores, um processo satura em cerca de **165 req/s**. Como cada sensor espera a resposta antes da próxima leitura, a taxa fica abaixo dos 250 req/s esperados e a latência sobe para cerca de 3 s, **sem erros**. O p95 passa da meta de 500 ms.
- O gargalo é a CPU do processo Python. Durante esse cenário, o uvicorn ficou em cerca de 120% de CPU, e os processos do PostgreSQL ficaram abaixo de 10% cada. Com `INTERVALO=1`, o teto foi o mesmo (cerca de 165 req/s). Por isso, a capacidade cresce com mais processos, ou seja, mais réplicas atrás do Nginx, e não com ajustes no banco.

Com o `docker-compose.yml` (2 réplicas atrás do Nginx, mesma máquina, simulador parado, seed com 50 pacientes e `N_PACIENTES=50`), o cenário de 500 sensores por 40 s deu **238 req/s, p50 8,2 ms, p95 33,3 ms, p99 53,9 ms e 0% de erros** (9999 respostas `201`). A segunda réplica dobra a capacidade e tira a API da saturação: o p95 cai de cerca de 3,3 s para 33 ms.

A execução oficial do relatório é feita na VPS e fica com a infraestrutura ([integracao-infra.md](integracao-infra.md), seção 11). O resultado na VPS depende de haver CPU para as duas réplicas.

## 11. Simulador e imagens Docker

### 11.1 Simulador de sensores

`simulador/simulador.py`, só com a biblioteca padrão do Python. Cada paciente (`P001` até `P{N_PACIENTES}`, os mesmos códigos do seed) tem uma thread que, a cada `INTERVALO` segundos, gera uma leitura e a envia para `POST /leituras` com o header `X-API-Key`.

**Geração.** Cada sinal faz um *random walk* em volta da base do perfil do paciente: a cada passo o valor volta 10% em direção à base e recebe um ruído gaussiano. Os valores ficam presos dentro das faixas aceitas pela API, então o simulador nunca gera 422.

| Perfil | Pacientes | Base (FC, SpO₂, temperatura) | Efeito |
|---|---|---|---|
| estável | os demais | 75, 97,5, 36,6 | sem alertas, exceto pelas anomalias |
| febril | final 3 (P003, P013...) | 102, 96,5, 38,4 | `FEBRE` frequente |
| hipoxêmico | final 7 (P007, P017...) | 90, 91,0, 36,8 | `SPO2_BAIXA` de vez em quando |

Além disso, cerca de 2% das leituras de qualquer paciente têm uma queda pontual de SpO₂ (entre 82 e 89), que dispara `SPO2_BAIXA`.

**Buffer e reenvio.** O `id` (UUID v4) e o `ts` nascem na criação da leitura, e não no envio. A leitura entra num buffer local (`deque` com até 1000 itens por sensor) e só sai dele conforme a resposta:

| Resposta | O que o simulador faz |
|---|---|
| `201` (nova) ou `200` (já existia) | sucesso: tira a leitura do buffer |
| sem resposta (conexão recusada, DNS, timeout de 5 s), `5xx`, `408`, `429` | falha temporária: a leitura fica no buffer, e o sensor para de enviar até o próximo ciclo |
| outros `4xx` (`401`, `404`, `422`) | rejeição definitiva: reenviar não adianta, então a leitura é descartada com uma linha de WARNING |

O buffer é reenviado em ordem, da leitura mais antiga para a mais nova. Como o reenvio usa o mesmo `id`, uma leitura que a API gravou mas cuja resposta se perdeu volta como `200`, sem duplicar. Com o buffer cheio, a leitura mais antiga é descartada e contada como `perdidas`. Com 1000 itens e `INTERVALO=2`, cada sensor aguenta cerca de 33 minutos de API fora.

**Logs** (para as evidências dos testes de falha):

```
2026-11-25T15:58:44+0000 INFO [simulador] resumo: enviadas=100 novas=100 duplicadas=0 rejeitadas=0 falhas=0 perdidas=0 buffer=0
2026-11-25T15:58:56+0000 WARNING [simulador] API indisponível (URLError: ...); leituras ficam no buffer
2026-11-25T15:59:02+0000 INFO [simulador] API disponível novamente; reenviando o buffer
2026-11-25T15:59:16+0000 INFO [simulador] resumo final: enviadas=275 novas=275 duplicadas=0 rejeitadas=0 falhas=19 perdidas=0 buffer=0
```

O resumo sai a cada 10 s, com totais acumulados. `buffer` é a soma dos buffers de todos os sensores. A queda e a volta da API geram uma linha cada (não uma por sensor). No `SIGTERM`, o simulador escreve o resumo final e avisa quantas leituras ainda estavam no buffer, porque o buffer fica em memória e se perde no encerramento.

### 11.2 Imagens Docker

| Imagem | Dockerfile | Conteúdo | Comando padrão |
|---|---|---|---|
| backend | `backend/Dockerfile` | `app/`, `migrations/`, `alembic.ini`, `uvicorn-log.json` e as dependências de `requirements.txt` | a API (uvicorn) |
| simulador | `simulador/Dockerfile` | só `simulador.py`, sem dependências | `python simulador.py` |

As duas partem de `python:3.12-slim` e rodam com o usuário `app` (`uid 10001`). A imagem do backend é **única** para `api`, `worker` e `migrate`: o Compose só troca o comando (ver [integracao-infra.md](integracao-infra.md#1-serviços-do-backend)). Por isso ela não declara `HEALTHCHECK`: o worker não tem porta e ficaria `unhealthy`. O healthcheck da API fica no Compose.

O `uvicorn-log.json` põe os logs do uvicorn no mesmo formato dos logs da aplicação, com timestamp e em stdout (os de acesso também).

### 11.3 Docker Compose

O [docker-compose.yml](../docker-compose.yml) da raiz sobe o sistema inteiro: `postgres` (volume `pgdata`, sem porta publicada), `migrate` (one-shot), `api` com 2 réplicas e healthcheck, `worker`, `simulador` e `nginx`. A ordem é `postgres` healthy → `migrate` concluído com sucesso → `api` e `worker` → `nginx` (depois das réplicas healthy) → `simulador`. Foi feito pelo backend a pedido da infra, que passa a mantê-lo. Como subir: [integracao-infra.md](integracao-infra.md), seção 0. Ensaio: seção 10.3.

Decisões:

- **Variáveis por serviço:** cada serviço recebe só o que usa, em vez de um `env_file` com todos os segredos em todos os containers. As obrigatórias usam `${VAR:?}`, então o compose não sobe sem elas.
- **`DATABASE_URL` montada** a partir de `POSTGRES_USER`, `POSTGRES_PASSWORD` e `POSTGRES_DB`, para a senha existir num lugar só. Por isso a senha precisa ser só letras e números.
- **Nginx mínimo** em [infra/nginx/default.conf](../infra/nginx/default.conf): balanceamento entre as réplicas, `proxy_connect_timeout 2s` e retentativa na outra réplica para as rotas idempotentes, sem `http_503`, para o 503 da API (banco fora) chegar ao cliente. HTTPS e rate limit ficam com a infra.
- **Rotação de logs** (`json-file`, 3 × 10 MB por serviço), para os logs não encherem o disco da VPS.

## 12. Pendências

Backend: textos do relatório (etapa 9).

Pontos para a infraestrutura, já descritos em [integracao-infra.md](integracao-infra.md):

- HTTPS e rate limit no Nginx ([infra/nginx/default.conf](../infra/nginx/default.conf) é a versão mínima).
- Rate limit também no `POST /auth/cadastro`, que hoje não tem limite no Nginx ([integracao-infra.md](integracao-infra.md), seção 4).
- O CI precisa de um serviço `postgres:16` e da variável `TEST_DATABASE_URL`, e pode rodar também os testes do simulador.
- Teste de desempenho na VPS: `SEED_PACIENTES=500`, rate limit de `/leituras` que comporte 250 req/s de um único IP (ou desligado durante o teste), simulador parado e `docker stats` durante cada cenário.
