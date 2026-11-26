#!/bin/bash
# Backup diário do banco: pg_dump compactado, 7 dias, só na VPS.
# Sem cópia no B2 de propósito: o bucket é dos outros projetos da VPS, e este fica isolado deles.
# Cron na VPS (crontab -e):
#   30 3 * * * $HOME/projetos/sspd/infra/backup/backup.sh >> $HOME/backups/sspd/backup.log 2>&1
# Restaurar: docs/infra.md, seção "Backups".
set -euo pipefail

DESTINO="$HOME/backups/sspd"
ARQUIVO="$DESTINO/sspd_$(date +%F_%H-%M).sql.gz"
mkdir -p "$DESTINO"
cd "$(dirname "$0")/../.."

echo "[$(date -Is)] backup: início"
# Grava em .tmp e só renomeia no fim: um dump que falhou no meio nunca parece um backup válido.
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$ARQUIVO.tmp"
mv "$ARQUIVO.tmp" "$ARQUIVO"
echo "[$(date -Is)] backup: $ARQUIVO ($(du -h "$ARQUIVO" | cut -f1))"

find "$DESTINO" -name 'sspd_*.sql.gz' -mtime +7 -delete

