#!/usr/bin/env bash
# Encrypted, coordinated backup of PostgreSQL + MinIO + published configuration.
#
# Produces a timestamped backup directory under an isolated target (default
# ./backups/). Every artifact is AES-256-CBC encrypted (openssl, PBKDF2) with
# a passphrase file unless --no-encrypt is given (rehearsal only), and a
# SHA-256 hash manifest is written last so integrity can be verified.
#
# Baseline per the release ticket: RPO 24h / RTO 4h. See
# docs/ops/backup-restore.md for the schedule, rehearsal, and restore steps.
#
# Usage:
#   scripts/backup.sh [--compose FILE] [--target DIR] [--passphrase-file FILE]
#                     [--no-encrypt] [--verify] [--keep N]
#
# Options:
#   --compose FILE          compose file (default: docker-compose.prod.yml)
#   --target DIR            isolated backup target directory (default ./backups)
#   --passphrase-file FILE  passphrase used to encrypt artifacts (required
#                           unless --no-encrypt)
#   --no-encrypt            store plaintext artifacts (rehearsals only)
#   --verify                verify the hash manifest after the backup
#   --keep N                prune older backups in the target, keeping N
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="docker-compose.prod.yml"
TARGET_DIR="$REPO_ROOT/backups"
PASSPHRASE_FILE=""
ENCRYPT=1
VERIFY=0
KEEP=""

usage() {
  sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose) COMPOSE_FILE="$2"; shift 2 ;;
    --target) TARGET_DIR="$2"; shift 2 ;;
    --passphrase-file) PASSPHRASE_FILE="$2"; shift 2 ;;
    --no-encrypt) ENCRYPT=0; shift ;;
    --verify) VERIFY=1; shift ;;
    --keep) KEEP="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done

if [[ "$ENCRYPT" -eq 1 ]]; then
  [[ -n "$PASSPHRASE_FILE" ]] || { echo "error: --passphrase-file is required (or use --no-encrypt for rehearsals)" >&2; exit 2; }
  [[ -f "$PASSPHRASE_FILE" ]] || { echo "error: passphrase file not found: $PASSPHRASE_FILE" >&2; exit 2; }
  ENC_ARGS=(-aes-256-cbc -pbkdf2 -iter 100000 -pass "file:$PASSPHRASE_FILE")
else
  echo "warning: --no-encrypt stores plaintext artifacts; rehearsal only" >&2
fi

COMPOSE_FILE_ABS="$REPO_ROOT/$COMPOSE_FILE"
[[ -f "$COMPOSE_FILE_ABS" ]] || { echo "error: compose file not found: $COMPOSE_FILE_ABS" >&2; exit 2; }

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$TARGET_DIR/$STAMP-icrm-backup"
mkdir -p "$BACKUP_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> backup directory: $BACKUP_DIR"

# --- PostgreSQL -------------------------------------------------------------
echo "==> dumping PostgreSQL (icrm)"
if ! docker compose -f "$COMPOSE_FILE_ABS" exec -T postgres \
  sh -c 'PGPASSWORD="$(cat /run/secrets/postgres_password 2>/dev/null || echo "$POSTGRES_PASSWORD")" pg_dump --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > "$TMP/postgres.sql"; then
  echo "error: pg_dump failed (is the compose stack running? try docker compose up -d)" >&2
  exit 1
fi

# --- MinIO ------------------------------------------------------------------
echo "==> mirroring MinIO bucket (materials)"
POSTGRES_CONTAINER="$(docker compose -f "$COMPOSE_FILE_ABS" ps -q postgres)"
if [[ -z "$POSTGRES_CONTAINER" ]]; then
  echo "error: postgres container not running" >&2
  exit 1
fi
NETWORK="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$POSTGRES_CONTAINER" | awk '{print $1}')"
if [[ -z "$NETWORK" ]]; then
  echo "error: cannot determine compose network" >&2
  exit 1
fi
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
  -v "$TMP:/backup" \
  minio/mc:RELEASE.2025-02-08T19-14-21Z mirror --overwrite local/materials /backup/materials \
  || { echo "error: MinIO mirror failed" >&2; exit 1; }

# --- Published configuration ------------------------------------------------
echo "==> copying published configuration"
mkdir -p "$TMP/config"
for file in docker-compose.yml docker-compose.prod.yml .env.example .env.prod.example; do
  if [[ -f "$REPO_ROOT/$file" ]]; then
    cp "$REPO_ROOT/$file" "$TMP/config/$file"
  fi
done

# --- Encrypt + manifest -----------------------------------------------------
if [[ "$ENCRYPT" -eq 1 ]]; then
  echo "==> encrypting artifacts"
  # Encrypt every artifact under $TMP: postgres.sql, config/*, materials/*.
  while IFS= read -r artifact; do
    relative="${artifact#"$TMP"/}"
    mkdir -p "$BACKUP_DIR/$(dirname "$relative")"
    openssl enc "${ENC_ARGS[@]}" -in "$artifact" -out "$BACKUP_DIR/$relative.enc"
  done < <(find "$TMP" -type f -print)
else
  echo "==> copying artifacts (plaintext, rehearsal only)"
  cp -a "$TMP"/. "$BACKUP_DIR"/
fi

# --- Component metadata + hash manifest -------------------------------------
POSTGRES_BYTES="$(stat -c %s "$TMP/postgres.sql" 2>/dev/null || stat -f %z "$TMP/postgres.sql")"
MATERIALS_BYTES="$(du -sb "$TMP/materials" 2>/dev/null | awk '{print $1}' || echo unknown)"
CONFIG_LIST="$(cd "$TMP/config" && find . -type f | sort | sed 's/^\.\///' | tr '\n' ' ')"
cat > "$BACKUP_DIR/backup.json" <<EOF
{
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "engine": "icrm-backup-script",
  "encrypted": $([ "$ENCRYPT" -eq 1 ] && echo true || echo false),
  "postgresql_dump_bytes": "$POSTGRES_BYTES",
  "minio_materials_bytes": "$MATERIALS_BYTES",
  "config_files": "$CONFIG_LIST"
}
EOF

( cd "$BACKUP_DIR" && find . -type f ! -name MANIFEST.sha256 | sort | xargs sha256sum > MANIFEST.sha256 )
echo "==> backup complete: $BACKUP_DIR"

if [[ "$VERIFY" -eq 1 ]]; then
  ( cd "$BACKUP_DIR" && sha256sum -c MANIFEST.sha256 )
  echo "==> manifest verified"
fi

if [[ -n "$KEEP" ]]; then
  ls -1d "$TARGET_DIR"/*-icrm-backup 2>/dev/null | sort -r | tail -n +$((KEEP + 1)) | while IFS= read -r old; do
    echo "==> pruning $old"
    rm -rf "$old"
  done
fi
