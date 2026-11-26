---
paths:
  - "backend/app/**/*.py"
  - "backend/tests/**/*.py"
---

# Estrutura do repositório

A árvore abaixo é a estrutura **alvo**. Em 26/11/2026 ainda não existem `dashboard/` (por enquanto, em outro repositório) e o resto de `infra/` (só há `infra/nginx/default.conf`).

```
/
├── CLAUDE.md
├── README.md
├── docker-compose.yml          # raiz: sistema completo (feito pelo Gerson a pedido da infra, mantido pela infra)
├── .env.example
├── backend/
│   ├── Dockerfile              # uma imagem para api, worker e migrate
│   ├── pyproject.toml / requirements.txt
│   ├── alembic.ini
│   ├── migrations/
│   ├── app/
│   │   ├── dominio/
│   │   │   ├── entidades.py        # Leitura, Alerta, Paciente, Usuario, ResumoPaciente, ResultadoCiclo (dataclasses)
│   │   │   ├── regras.py           # avaliar(leitura) -> list[Alerta], função pura
│   │   │   ├── erros.py            # PacienteNaoEncontrado, LeituraInvalida, ...
│   │   │   └── portas.py           # Protocols (repositórios, fila de pendentes)
│   │   ├── aplicacao/
│   │   │   ├── registrar_leitura.py
│   │   │   ├── processar_pendentes.py
│   │   │   ├── reconhecer_alerta.py
│   │   │   ├── autenticar.py       # login: confere e-mail e senha (bcrypt injetado)
│   │   │   ├── cadastrar_paciente.py
│   │   │   └── consultas.py        # listar pacientes, histórico, alertas
│   │   ├── adaptadores/
│   │   │   ├── entrada/
│   │   │   │   ├── http/
│   │   │   │   │   ├── main.py     # criar_app(componentes, ...) -> FastAPI (recebe tudo pronto)
│   │   │   │   │   ├── schemas.py  # Pydantic (só aqui)
│   │   │   │   │   ├── seguranca.py# API key, JWT, hash de senha
│   │   │   │   │   ├── deps.py     # Depends que entregam os casos de uso
│   │   │   │   │   └── routers/    # leituras, pacientes, alertas, auth, health
│   │   │   │   └── worker/
│   │   │   │       └── main.py     # loop de polling (entrypoint do worker)
│   │   │   └── saida/
│   │   │       └── postgres/
│   │   │           ├── db.py       # engine, sessão, retry de conexão
│   │   │           ├── models.py   # tabelas SQLAlchemy (só aqui)
│   │   │           ├── mapeamento.py   # model <-> entidade
│   │   │           ├── repositorios.py
│   │   │           └── fila_pendentes.py
│   │   ├── config.py               # variáveis de ambiente
│   │   ├── composicao.py           # monta adaptadores + casos de uso (DI manual); criar_app_http (uvicorn) e rodar_worker (python -m app.composicao worker)
│   │   └── seed.py                 # cria usuário admin e pacientes fictícios (idempotente)
│   └── tests/
│       ├── unit/                   # domínio, aplicação e HTTP (test_http.py), com fakes em memória
│       ├── fakes.py                # implementações em memória das portas
│       └── integracao/             # adaptadores Postgres e rotas HTTP, banco real (apoio.py = helpers)
├── simulador/
│   ├── Dockerfile
│   └── simulador.py
├── tests-carga/
│   └── leituras.js             # script k6
├── dashboard/                  # amigo 2
├── infra/                      # amigo 1 (nginx/default.conf mínimo já existe)
└── docs/                       # relatório, prints, gráficos
```

## Arquitetura do backend: hexagonal leve

**Objetivo:** separar as responsabilidades por contratos (portas). A API HTTP e o worker são dois **adaptadores de entrada** diferentes que usam o mesmo núcleo (domínio + aplicação). O Postgres é um **adaptador de saída**.

```
   [FastAPI / HTTP] ──┐                         ┌── [Postgres: repositórios]
                      ├─> aplicacao ─> dominio <┤
   [Worker / polling]─┘   (casos de uso) (portas)└── [Postgres: fila de pendentes]
   adaptadores de entrada                           adaptadores de saída
```

**Regra de dependência (obrigatória):**

| Camada | Pode importar | Não pode importar |
|---|---|---|
| `dominio` | só a stdlib | `aplicacao`, `adaptadores`, FastAPI, SQLAlchemy, Pydantic |
| `aplicacao` | `dominio` | `adaptadores`, FastAPI, SQLAlchemy, Pydantic |
| `adaptadores/entrada` | `aplicacao`, `dominio` | `adaptadores/saida`, `composicao` (recebe tudo pronto de `composicao.py`) |
| `adaptadores/saida` | `dominio` | `aplicacao`, `adaptadores/entrada`, `composicao` |
| `composicao.py` | tudo | é o único lugar que conhece as implementações concretas |

Se for preciso quebrar essa regra, parar e perguntar antes.

**Portas:** `typing.Protocol` em `backend/app/dominio/portas.py`, que é a fonte das assinaturas (status e tipos de alerta usam os enums `StatusAlerta` e `TipoAlerta`). São elas: `RepositorioPacientes`, `RepositorioLeituras`, `RepositorioAlertas`, `RepositorioUsuarios`, `LotePendente` e `FilaPendentes`.

**Contrato de `FilaPendentes.reservar` (crítico para a tolerância a falhas):**
- Ao entrar: abre a transação e executa `SELECT ... WHERE processada = false ORDER BY ts LIMIT :limite FOR UPDATE SKIP LOCKED`.
- Tudo o que o `LotePendente` fizer acontece **na mesma transação**.
- Saída sem exceção → `COMMIT`. Saída com exceção → `ROLLBACK`, e o lote volta a ficar pendente.
- O caso de uso nunca vê SQL, sessão ou transação.

O `salvar_se_nova` usa `INSERT ... ON CONFLICT (id) DO NOTHING` e devolve se inseriu. A idempotência é semântica da porta; o SQL fica no adaptador.

**Casos de uso (`aplicacao/`):** classes pequenas que recebem as portas no construtor e expõem um método `executar(...)`.

```python
class RegistrarLeitura:
    def __init__(self, pacientes: RepositorioPacientes, leituras: RepositorioLeituras,
                 relogio: Callable[[], datetime]): ...
    def executar(self, leitura: Leitura) -> bool:   # True = nova, False = duplicada
        # valida ts com relogio() (dominio), garante paciente existente, salvar_se_nova

class ProcessarPendentes:
    def __init__(self, fila: FilaPendentes): ...
    def executar(self, limite: int) -> ResultadoCiclo:  # processadas, alertas_criados, backlog
        # with fila.reservar(limite) as lote: avaliar regras, anti-spam, registrar, marcar
        # depois do commit: backlog = fila.contar_pendentes()
```

**Onde mora cada coisa:**
- Validação de faixa (fc, spo2, temp) é regra de domínio e fica no `__post_init__` da entidade, de modo que uma `Leitura` inválida não chega a existir. O Pydantic do adaptador HTTP só valida tipo e formato.
- A regra "`ts` no máximo 5 min no futuro" depende do relógio. Por isso fica em `Leitura.validar_horario(agora)`, e `RegistrarLeitura` recebe um `relogio: Callable[[], datetime]` no construtor. Assim o teste não precisa de mock de `datetime`. A regra não fica no `__post_init__` porque uma leitura antiga reenviada pelo buffer continua válida.
- Erros de domínio (`PacienteNaoEncontrado`, `LeituraInvalida`) são exceções do domínio. O adaptador HTTP converte em 404/422 com um exception handler.
- JWT, API key, bcrypt e CORS são preocupação do adaptador HTTP e **não** têm porta. O `Autenticar` recebe `verificar_senha(senha, hash | None) -> bool` no construtor (implementada em `seguranca.py`, ligada pelo `composicao.py`); o token é emitido pelo router de auth.
- O `/health` não tem caso de uso. O `composicao.py` injeta na rota uma função `verificar_banco() -> bool`, implementada em `db.py`. A rota não importa o adaptador de saída.
- O adaptador HTTP recebe do `composicao.py` um `Componentes` (dataclass em `http/deps.py`, guardado em `app.state`) com os casos de uso e o `verificar_banco`. Para adicionar um caso de uso a uma rota: campo novo em `Componentes`, uma função em `deps.py` e a montagem em `composicao.montar_componentes_http`.
- O HTTP não importa SQLAlchemy. As exceções que significam "banco fora" (`OperationalError` e `TimeoutError` do pool) chegam por `criar_app(..., erros_indisponibilidade=...)` e viram 503.
- O `seed.py` é um entrypoint de infraestrutura, como o `composicao.py`, e por isso fala direto com o adaptador Postgres.
- O teste `tests/unit/test_arquitetura.py` verifica a regra de dependência via `ast`, para todas as camadas da tabela acima.
- Logs de ciclo do worker ficam no adaptador de entrada `worker/`, a partir do `ResultadoCiclo` (que já traz o `backlog`).

**Fora de escopo nesta arquitetura (não fazer):**
- Container ou framework de injeção de dependência. Composição manual em `composicao.py` + `Depends` do FastAPI.
- DTO por camada. O schema Pydantic converte direto para a entidade e de volta.
- Porta para trocar de banco "no futuro". As portas existem para testar e para separar responsabilidades.
- Interfaces com uma única função trivial só por formalidade.
