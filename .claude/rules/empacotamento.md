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
- O `docker-compose.yml` é do Amigo 1. Contrato para ele: `docs/integracao-infra.md` (seções 1 a 3.1)

Ainda com a infra (avisar, não editar `/infra`):
- Healthcheck da API com `python -c "import urllib.request; ..."` (a imagem não tem `curl`)
- Nginx repassando `/docs` e `/openapi.json`; porta 8000 da API não publicada (`--forwarded-allow-ips=*` depende disso)
- GitHub Actions: serviço `postgres:16` e `TEST_DATABASE_URL`; testes do simulador em `simulador/`
