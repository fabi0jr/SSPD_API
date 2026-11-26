---
paths:
  - "**/Dockerfile"
  - "**/docker-compose*.yml"
  - "backend/requirements.txt"
---

# Comandos dos serviços (para o Dockerfile e o Compose)

Todos rodam dentro de `backend/`:

| Serviço | Comando |
|---|---|
| migrate | `alembic upgrade head && python -m app.seed` |
| api | CMD padrão do `backend/Dockerfile`: `uvicorn app.composicao:criar_app_http --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=* --log-config uvicorn-log.json` |
| worker | `python -m app.composicao worker` (forma exec no Compose, sem `sh -c`, para receber o SIGTERM) |

Imagens (etapa 7, ensaiadas com `docker run` numa rede local; resultado em `docs/backend.md`, seção 10.3):
- `backend/Dockerfile`: uma imagem para os três serviços, `WORKDIR /app` (= conteúdo de `backend/`), usuário `app` (`uid 10001`), código do root. **Sem `HEALTHCHECK`** na imagem, porque o worker usa a mesma imagem e não tem porta: o healthcheck fica no Compose
- `backend/uvicorn-log.json`: formato dos logs do uvicorn igual ao da aplicação (com timestamp, stdout). Configura o logger root, então o `basicConfig` de `composicao.py` vira no-op com o mesmo formato
- `simulador/Dockerfile`: só `simulador.py`, sem pip install
- `.dockerignore` nas duas pastas: nada de `.venv`, `tests` ou `.env` na imagem

Compose (feito pelo Gerson a pedido do Amigo 1, que passa a mantê-lo; como subir: `docs/integracao-infra.md`, seção 0; decisões: `docs/backend.md`, seção 11.3):
- `docker-compose.yml` na raiz, `name: sspd`: `postgres` (volume `pgdata`, sem `ports`), `migrate` (one-shot, `restart: "no"`), `api` com `deploy.replicas: 2` e healthcheck `python -c "import urllib.request; ..."` (a imagem não tem `curl`), `worker` em forma exec, `simulador` (`API_URL` fixo em `http://nginx/leituras`) e `nginx:1.28-alpine` (só ele publica porta, `HTTP_PORT`, padrão 80)
- Variáveis **por serviço** (bloco `environment` com `${VAR:?}` nas obrigatórias), nunca `env_file` com tudo. `DATABASE_URL` montada de `POSTGRES_USER/PASSWORD/DB` (senha só alfanumérica)
- Porta 8000 da API nunca publicada (`--forwarded-allow-ips=*` depende disso). Logs com rotação (`x-logs`)
- `infra/nginx/default.conf` (mínimo): upstream `api:8000` (DNS do Docker devolve as 2 réplicas), `proxy_connect_timeout 2s`, `proxy_next_upstream error timeout http_502` (+ `non_idempotent` só em `/leituras` e `/alertas`), **sem `http_503`** (o 503 da API com `Retry-After` precisa chegar ao cliente). `location /alertas` sem barra final (com barra, `GET /alertas` dá 301). O compose monta a **pasta** `infra/nginx` (bind mount de arquivo fica preso à versão antiga)

Ainda com a infra (avisar, não editar `/infra` sem pedido):
- HTTPS e rate limit no Nginx
- GitHub Actions: serviço `postgres:16` e `TEST_DATABASE_URL`; testes do simulador em `simulador/`
