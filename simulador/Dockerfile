# Simulador de sensores. Só usa a biblioteca padrão: não há pip install.
# Build (na raiz do repositório):  docker build -t sspd-simulador ./simulador
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY simulador.py .

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
USER app

# Forma exec: o Python recebe o SIGTERM do `docker stop` e escreve o resumo final.
CMD ["python", "simulador.py"]
