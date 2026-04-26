#!/bin/bash
# Daily SQLite backup using the online-backup API (safe under WAL).
# Designed for cron — keeps a rolling 30-day window of dated copies.
#
# Usage:   bin/backup-db.sh
# Env:     BACKUP_DIR (default: $HOME/backups)
#          SOURCE_DB  (default: $HOME/rentero/cache/rentero.db)
#          KEEP_DAYS  (default: 30)
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
SOURCE_DB="${SOURCE_DB:-$HOME/rentero/cache/rentero.db}"
KEEP_DAYS="${KEEP_DAYS:-30}"

mkdir -p "$BACKUP_DIR"

DEST="$BACKUP_DIR/rentero_$(date +%Y%m%d).db"

python3 - "$SOURCE_DB" "$DEST" <<'PY'
import sqlite3
import sys

src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(src_path)
dst = sqlite3.connect(dst_path)
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
PY

find "$BACKUP_DIR" -name 'rentero_*.db' -mtime "+${KEEP_DAYS}" -delete
