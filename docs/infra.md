# Infraestrutura e segurança

O que roda na VPS, como chega lá e como é protegido. O contrato do backend com a infra fica em [integracao-infra.md](integracao-infra.md).

**Endereço:** https://sspd.fj-dev.tech (dashboard em `/`, API na raiz e em `/api/*`, Swagger em `/docs`).

## Visão geral

```
Internet ──443──> [UFW] ──> [Traefik] ──HTTP, rede infra-network──> [Nginx do projeto] ──> api ×2 ──> postgres
           80 ──> (redireciona/ACME)   HTTPS (Let's Encrypt)            rate limit, balanceamento       worker ─┘
                                                                     dashboard estático
```

- **VPS:** Ubuntu, 2 vCPUs, 8 GB de RAM, Docker Engine + Compose. Outros projetos dividem a máquina.
- **Traefik** (já existente na VPS, o único container que publica as portas 80/443): termina o HTTPS com certificado Let's Encrypt renovado sozinho e roteia `sspd.fj-dev.tech` para o Nginx do projeto pelas labels em [docker-compose.prod.yml](../docker-compose.prod.yml).
- **Nginx** ([infra/nginx/default.conf](../infra/nginx/default.conf)): balanceia entre as 2 réplicas da API com retentativa na outra réplica, aplica rate limit por IP (login e leituras), adiciona os cabeçalhos de segurança (HSTS, `nosniff`, `X-Frame-Options`, `Referrer-Policy`) e serve o dashboard. O IP real do cliente vem do `X-Forwarded-For`, aceito só quando chega da rede do Traefik.
- **Isolamento:** só o Nginx entra na rede `infra-network`. `api`, `worker` e `postgres` ficam na rede interna do projeto, sem nenhuma porta publicada.

## Arquivos

| Arquivo | Para quê |
|---|---|
| `docker-compose.yml` | todos os serviços (local e VPS) |
| `docker-compose.prod.yml` | override da VPS: tira a porta do Nginx e liga no Traefik |
| `infra/nginx/default.conf` | proxy, balanceamento, rate limit, dashboard |
| `infra/deploy.sh` | deploy na VPS (o único comando da chave do CI) |
| `infra/backup/backup.sh` | backup diário do banco |
| `infra/seguranca/hardening.sh` | SSH por chave, UFW e fail2ban |
| `.github/workflows/ci-cd.yml` | testes, deploy e smoke test |

## CI/CD (GitHub Actions)

1. **Todo PR e push** em `dev`/`main`: testes do backend (unitários + integração com um Postgres 16 descartável), testes do simulador e validação do compose de produção.
2. **Push na `main`** com os testes verdes: o job `deploy` entra na VPS por SSH e roda `infra/deploy.sh` (`git pull`, `docker compose up -d --build --wait`, reload do Nginx). Depois, um smoke test pela internet exige `200` no `/health` e `401` no `/pacientes`.

**A chave do CI não tem shell.** No `~/.ssh/authorized_keys` da VPS, ela tem a restrição abaixo. Qualquer comando enviado com ela é trocado pelo `deploy.sh`, e ela não abre túnel nem terminal:

```
command="~/projetos/sspd/infra/deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... sspd-ci
```

Secrets do repositório: `VPS_HOST`, `VPS_USER` e `VPS_SSH_KEY` (a chave privada). Os segredos da aplicação ficam só no `.env` da VPS (`chmod 600`), nunca no GitHub.

## Segurança da VPS

Aplicada por [infra/seguranca/hardening.sh](../infra/seguranca/hardening.sh) (`sudo bash infra/seguranca/hardening.sh`):

| Item | Configuração |
|---|---|
| SSH | só chave (`PasswordAuthentication no`), root sem login, 3 tentativas por conexão |
| UFW | entrada negada por padrão; liberadas só SSH, 80 e 443 |
| fail2ban | jail `sshd`: 5 erros em 10 min bane o IP por 1 h |
| HTTPS | Traefik + Let's Encrypt; HSTS no Nginx |
| Rate limit | Nginx, por IP: `/auth/login` (força bruta) e `/leituras` |

> **Docker e UFW:** portas publicadas com `ports:` passam por fora do UFW (o Docker escreve direto no iptables). Por isso nenhum serviço do projeto publica porta em produção: o único ponto de entrada é o Traefik.

Evidências para o relatório (na VPS):

```bash
sudo ufw status verbose
sudo fail2ban-client status sshd
sudo sshd -T | grep -Ei 'passwordauthentication|permitrootlogin'
ssh -o PubkeyAuthentication=no fabio@sspd.fj-dev.tech   # deve recusar: Permission denied (publickey)
curl -sI https://sspd.fj-dev.tech/health                # HTTP/2 200 + strict-transport-security
for i in $(seq 12); do curl -s -o /dev/null -w '%{http_code} ' -X POST https://sspd.fj-dev.tech/auth/login -H 'Content-Type: application/json' -d '{}'; done   # 422... depois 429
```

## Backups

`infra/backup/backup.sh`, todo dia às 03:30 pelo cron: `pg_dump` compactado em `~/backups/sspd` (7 dias) e cópia no Backblaze B2 (7 dias).

Restaurar (apaga e recria os dados do banco):

```bash
cd ~/projetos/sspd
docker compose stop api worker simulador
gunzip -c ~/backups/sspd/sspd_AAAA-MM-DD_HH-MM.sql.gz \
  | docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" -f -'
docker compose start api worker simulador
```

## Primeira instalação na VPS (feita uma vez)

```bash
git clone https://github.com/fabi0jr/SSPD_API.git ~/projetos/sspd && cd ~/projetos/sspd
cp .env.example .env && chmod 600 .env   # segredos com openssl rand -hex 32; descomentar COMPOSE_FILE; CORS_ORIGINS vazio
sh infra/deploy.sh
```
