#!/usr/bin/env bash
# Restore an ICRM-CA backup (PostgreSQL + MinIO + published configuration).
#
# Non-production rehearsal is the supported use; restoring over a live
# production stack requires an explicit --force. See docs/ops/backup-restore.md
# for the rehearsal procedure and the RPO 24h / RTO 4h baseline.
#
# Usage:
#   scripts/restore.sh --backup DIR [--compose FILE] [--passphrase-file FILE]
#                      [--stage DIR] [--force]
#
# Options:
#   --backup DIR            backup directory created by scripts/backup.sh
#   --compose FILE          compose file (default: docker-compose.prod.yml)
#   --passphrase-file FILE  passphrase used to decrypt artifacts
#   --stage DIR             directory for decrypted/staged files
#                           (default: <backup dir>/_restore-stage)
#   --force                 allow restoring into a running (production) stack
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="docker-compose.prod.yml"
BACKUP_DIR=""
PASSPHRASE_FILE=""
STAGE_DIR=""
FORCE=0

usage() {
  sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup) BACKUP_DIR="$2"; shift 2 ;;
    --compose) COMPOSE_FILE="$2"; shift 2 ;;
    --passphrase-file) PASSPHRASE_FILE="$2"; shift 2 ;;
    --stage) STAGE_DIR="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done

[[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] || { echo "error: --backup DIR is required" >&2; usage; }
BACKUP_DIR="${BACKUP_DIR%/}"
[[ -f "$BACKUP_DIR/MANIFEST.sha256" ]] || { echo "error: no MANIFEST.sha256 in $BACKUP_DIR" >&2; exit 2; }

# Detect whether the backup is encrypted from its own metadata.
ENCRYPTED=0
if [[ -f "$BACKUP_DIR/backup.json" ]] && grep -q '"encrypted": true' "$BACKUP_DIR/backup.json"; then
  ENCRYPTED=1
fi
if [[ "$ENCRYPTED" -eq 1 ]]; then
  [[ -n "$PASSPHRASE_FILE" ]] || { echo "error: --passphrase-file is required for this backup" >&2; exit 2; }
  [[ -f "$PASSPHRASE_FILE" ]] || { echo "error: passphrase file not found: $PASSPHRASE_FILE" >&2; exit 2; }
fi

# --- Verify hash manifest ----------------------------------------------------
echo "==> verifying hash manifest"
( cd "$BACKUP_DIR" && sha256sum -c MANIFEST.sha256 ) || {
  echo "error: hash manifest verification failed; refusing to restore" >&2
  exit 1
}

# --- Decrypt / stage ---------------------------------------------------------
STAGE_DIR="${STAGE_DIR:-$BACKUP_DIR/_restore-stage}"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
if [[ "$ENCRYPTED" -eq 1 ]]; then
  echo "==> decrypting into $STAGE_DIR"
  while IFS= read -r artifact; do
    relative="${artifact#"$BACKUP_DIR"/}"
    destination="$STAGE_DIR/${relative%.enc}"
    mkdir -p "$(dirname "$destination")"
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
      -pass "file:$PASSPHRASE_FILE" -in "$artifact" -out "$destination"
  done < <(find "$BACKUP_DIR" -type f -name '*.enc' -print)
else
  echo "==> staging plaintext backup"
  cp -a "$BACKUP_DIR"/. "$STAGE_DIR"/
fi

[[ -f "$STAGE_DIR/postgres.sql" ]] || { echo "error: no postgres.sql in backup" >&2; exit 2; }
[[ -d "$STAGE_DIR/materials" ]] || { echo "error: no materials/ in backup" >&2; exit 2; }

# --- Restore targets ---------------------------------------------------------
if [[ "$FORCE" -ne 1 ]]; then
  echo "warning: refusing to restore into a running stack without --force" >&2
  echo "  (non-production rehearsal: stop the stack or use --force knowingly)" >&2
  exit 1
fi

COMPOSE_FILE_ABS="$REPO_ROOT/$COMPOSE_FILE"
[[ -f "$COMPOSE_FILE_ABS" ]] || { echo "error: compose file not found: $COMPOSE_FILE_ABS" >&2; exit 1; }

echo "==> restoring PostgreSQL (icrm)"
docker compose -f "$COMPOSE_FILE_ABS" exec -T postgres \
  sh -c 'PGPASSWORD="$(cat /run/secrets/postgres_password 2>/dev/null || echo "$POSTGRES_PASSWORD")" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$STAGE_DIR/postgres.sql"

echo "==> restoring MinIO bucket (materials)"
POSTGRES_CONTAINER="$(docker compose -f "$COMPOSE_FILE_ABS" ps -q postgres)"
NETWORK="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$POSTGRES_CONTAINER" | awk '{print $1}')"
if [[ -f "$REPO_ROOT/secrets/minio_root_user" ]] && [[ -f "$REPO_ROOT/secrets/minio_root_password" ]]; then
  MINIO_USER="$(cat "$REPO_ROOT/secrets/minio_root_user")"
  MINIO_PASSWORD="$(cat "$REPO_ROOT/secrets/minio_root_password")"
else
  MINIO_USER="${MINIO_ROOT_USER:-icrm-local}"
  MINIO_PASSWORD="${MINIO_ROOT_PASSWORD:-local-minio-password}"
fi
docker run --rm --user "$(id -u):$(id -g)" --network "$NETWORK" \
  -e HOME=/tmp \
  -e "MC_HOST_local=http://$MINIO_USER:$MINIO_PASSWORD@minio:9000" \
  -v "$STAGE_DIR:/backup" \
  --entrypoint /bin/sh \
  minio/mc:RELEASE.2025-02-08T19-14-21Z -c \
  'mc mb --ignore-existing local/materials && mc mirror --overwrite /backup/materials local/materials'

echo "==> configuration restored to $STAGE_DIR/config (apply manually)"
if [[ -d "$STAGE_DIR/config" ]]; then
  ls -la "$STAGE_DIR/config"
fi
echo "==> restore complete"
