#!/bin/sh
# Deploy na VPS. É o único comando que a chave do GitHub Actions consegue rodar:
# no ~/.ssh/authorized_keys da VPS ela tem command="~/projetos/sspd/infra/deploy.sh"
# (forced command), então ignora o que o workflow mandar e roda só isto.
#
# Chaves: o sh lê o script aos poucos, e o git pull pode trocar este arquivo no
# meio da execução. Dentro de { }, o bloco inteiro é lido antes de rodar.
{
  set -eu
  cd "$HOME/projetos/sspd"
  export COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml

  git pull --ff-only origin main
  docker compose up -d --build --wait
  # O nginx resolve "api" só ao iniciar; réplicas recriadas mudam de IP. O reload relê sem derrubar conexões.
  docker compose exec -T nginx nginx -s reload
  docker image prune -f
  echo "deploy ok: $(git rev-parse --short HEAD)"
  exit 0
}
