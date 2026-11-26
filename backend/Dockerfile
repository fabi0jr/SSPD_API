# Uma única imagem para api, worker e migrate: só muda o comando.
# O CMD padrão sobe a API; o Compose troca o comando no worker e no migrate:
#   worker:  ["python", "-m", "app.composicao", "worker"]   (forma exec, recebe o SIGTERM)
#   migrate: sh -c "alembic upgrade head && python -m app.seed"
#
# Sem HEALTHCHECK aqui de propósito: o worker usa a mesma imagem e não tem
# porta. O healthcheck da API fica no Compose (docs/integracao-infra.md, seção 3).
#
# Build (na raiz do repositório):  docker build -t sspd-backend ./backend
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependências antes do código: esta camada fica em cache enquanto o
# requirements.txt não mudar.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini uvicorn-log.json ./
COPY migrations ./migrations
COPY app ./app

# Usuário sem privilégios. O código continua pertencendo ao root, então o
# processo não consegue alterá-lo.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8000

# --proxy-headers: a API fica atrás do Nginx (IP real do cliente nos logs).
# --log-config: logs de acesso do uvicorn com timestamp, em stdout.
CMD ["uvicorn", "app.composicao:criar_app_http", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*", \
     "--log-config", "uvicorn-log.json"]
