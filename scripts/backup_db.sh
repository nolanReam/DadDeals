#!/usr/bin/env bash

# Back up DadDeals SQLite data.
#
# This copies only instance/daddeals.db into backups/. It never copies .env.
# The newest 10 backups are kept; older backup files are removed.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
DATABASE_PATH="${DATABASE_PATH:-${PROJECT_DIR}/instance/daddeals.db}"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_DIR}/backups}"
KEEP_COUNT="${KEEP_COUNT:-10}"

timestamp() {
    date "+%Y%m%d-%H%M%S"
}

if [ ! -f "${DATABASE_PATH}" ]; then
    echo "ERROR: Database file was not found: ${DATABASE_PATH}"
    echo "Run python app.py --init-db first, or check DATABASE_PATH."
    exit 1
fi

mkdir -p "${BACKUP_DIR}"

backup_file="${BACKUP_DIR}/daddeals-$(timestamp).db"
cp "${DATABASE_PATH}" "${backup_file}"

echo "Created backup:"
echo "  ${backup_file}"

# Keep only the newest N backups. Filenames sort chronologically.
find "${BACKUP_DIR}" -maxdepth 1 -type f -name "daddeals-*.db" \
    | sort -r \
    | awk -v keep="${KEEP_COUNT}" 'NR > keep { print }' \
    | while IFS= read -r old_backup; do
        rm -f "${old_backup}"
        echo "Removed old backup: ${old_backup}"
    done

echo "Backup complete. Kept the newest ${KEEP_COUNT} backup(s)."
