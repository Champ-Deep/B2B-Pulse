#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# AutoEngage Database Backup Script
# Usage: ./scripts/backup_db.sh
# Requires: pg_dump, gzip, and optionally aws CLI for S3/R2 upload
# =============================================================================

BACKUP_DIR="${BACKUP_DIR:-/tmp/autoengage-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/autoengage_${TIMESTAMP}.sql.gz"

# Parse DATABASE_URL into components (format: postgresql+asyncpg://user:pass@host:port/dbname)
parse_db_url() {
    local url="${DATABASE_URL:-}"
    # Strip driver prefix
    url="${url#*://}"
    DB_USER="${url%%:*}"
    url="${url#*:}"
    DB_PASS="${url%%@*}"
    url="${url#*@}"
    DB_HOST="${url%%:*}"
    url="${url#*:}"
    DB_PORT="${url%%/*}"
    DB_NAME="${url#*/}"
    # Strip query params
    DB_NAME="${DB_NAME%%\?*}"
}

echo "[$(date)] Starting database backup..."

mkdir -p "$BACKUP_DIR"
parse_db_url

PGPASSWORD="$DB_PASS" pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --format=custom \
    --no-owner \
    --no-privileges \
    | gzip > "$BACKUP_FILE"

FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date)] Backup created: $BACKUP_FILE ($FILE_SIZE)"

# Upload to S3/R2 if bucket is configured
if [ -n "${S3_BACKUP_BUCKET:-}" ]; then
    aws s3 cp "$BACKUP_FILE" "s3://${S3_BACKUP_BUCKET}/database/" \
        --endpoint-url "${S3_ENDPOINT_URL:-}" \
        2>/dev/null && echo "[$(date)] Uploaded to S3: s3://${S3_BACKUP_BUCKET}/database/" \
        || echo "[$(date)] WARNING: S3 upload failed"
fi

# Prune old local backups
DELETED=$(find "$BACKUP_DIR" -name "autoengage_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete -print | wc -l)
if [ "$DELETED" -gt 0 ]; then
    echo "[$(date)] Deleted $DELETED backup(s) older than ${RETENTION_DAYS} days"
fi

echo "[$(date)] Backup complete"
