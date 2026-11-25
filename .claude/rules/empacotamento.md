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
| api | `uvicorn app.composicao:criar_app_http --factory --host 0.0.0.0 --port 8000 --proxy-headers` |
| worker | `python -m app.composicao worker` (forma exec no Compose, sem `sh -c`, para receber o SIGTERM) |

Pendências para a infra, a resolver na etapa 7 (avisar o amigo da infra, não editar `/infra`):
- A imagem `python:3.12-slim` não tem `curl`: o healthcheck da API precisa usar `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"`.
- O Nginx precisa repassar `/docs` e `/openapi.json` para a API, para o Swagger funcionar na VPS.
- `--proxy-headers` porque a API fica atrás do Nginx.
- Os logs de acesso do uvicorn saem sem timestamp. Ajustar com `--log-config` no Dockerfile.
- GitHub Actions: serviço `postgres:16` e a variável `TEST_DATABASE_URL` para os testes de integração.
