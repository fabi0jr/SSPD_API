---
paths:
  - "backend/app/adaptadores/saida/**/*.py"
  - "backend/migrations/**"
  - "backend/alembic.ini"
  - "backend/app/seed.py"
  - "backend/tests/integracao/**/*.py"
---

# Modelo de dados

```sql
pacientes (
  id           VARCHAR(10) PRIMARY KEY,      -- "P001", mesmo código usado pelo simulador
  nome         VARCHAR(100) NOT NULL,        -- fictício
  leito        VARCHAR(20),
  criado_em    TIMESTAMPTZ DEFAULT now()
)

leituras (
  id           UUID PRIMARY KEY,             -- gerado pelo SENSOR (idempotência)
  paciente_id  VARCHAR(10) NOT NULL REFERENCES pacientes(id),
  fc           SMALLINT NOT NULL,            -- bpm
  spo2         NUMERIC(4,1) NOT NULL,        -- %
  temp         NUMERIC(3,1) NOT NULL,        -- °C
  ts           TIMESTAMPTZ NOT NULL,         -- hora da medição (sensor)
  recebido_em  TIMESTAMPTZ DEFAULT now(),    -- hora de chegada (servidor)
  processada   BOOLEAN NOT NULL DEFAULT false
)
-- índices: (paciente_id, ts DESC) e um índice parcial WHERE processada = false

alertas (
  id           BIGSERIAL PRIMARY KEY,
  paciente_id  VARCHAR(10) NOT NULL REFERENCES pacientes(id),
  leitura_id   UUID NOT NULL REFERENCES leituras(id),
  tipo         VARCHAR(30) NOT NULL,         -- SPO2_BAIXA, TAQUICARDIA, BRADICARDIA, FEBRE, HIPOTERMIA
  valor        NUMERIC(5,1) NOT NULL,
  severidade   VARCHAR(10) NOT NULL,         -- ALTA, MEDIA
  status       VARCHAR(15) NOT NULL DEFAULT 'aberto',  -- aberto | reconhecido
  criado_em    TIMESTAMPTZ DEFAULT now()
)
-- índices: (status, criado_em DESC) e o ÚNICO PARCIAL uq_alertas_aberto_por_tipo
--   (paciente_id, tipo) WHERE status = 'aberto'  -> no máximo um alerta aberto por paciente e tipo

usuarios (
  id           SERIAL PRIMARY KEY,
  email        VARCHAR(120) UNIQUE NOT NULL,
  senha_hash   VARCHAR(200) NOT NULL
)
```

`recebido_em - ts` fornece a latência ponta a ponta, que é útil no relatório.

Implementado na migration `migrations/versions/0001_inicial.py`. Diferenças em relação ao esboço acima: `criado_em` e `recebido_em` são `NOT NULL` (com default), e o índice único parcial dos alertas garante o anti-spam no próprio banco, mesmo com mais de um worker. O teste `test_migrations_batem_com_os_models` falha se `models.py` mudar sem migration nova.

O banco guarda `spo2` e `temp` com uma casa decimal (`NUMERIC`), então 97.43 vira 97.4. O worker avalia o valor já arredondado.
