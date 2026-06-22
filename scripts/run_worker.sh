#!/usr/bin/env bash

# DadDeals cron helper for Raspberry Pi.
#
# By default this script finds the DadDeals folder by looking one directory up
# from this script. If your Raspberry Pi uses a different layout, you can edit
# PROJECT_DIR below, for example:
# PROJECT_DIR="/home/pi/DadDeals"

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_DIR}/.venv/bin/python}"
LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/worker.log"

if [ ! -x "${PYTHON_BIN}" ] && [ -x "${PROJECT_DIR}/.venv/Scripts/python.exe" ]; then
    PYTHON_BIN="${PROJECT_DIR}/.venv/Scripts/python.exe"
fi

timestamp() {
    date "+%Y-%m-%d %H:%M:%S %z"
}

mkdir -p "${LOG_DIR}"

{
    echo "===== DadDeals worker start: $(timestamp) ====="
    echo "Project directory: ${PROJECT_DIR}"

    if [ ! -d "${PROJECT_DIR}" ]; then
        echo "ERROR: Project directory does not exist: ${PROJECT_DIR}"
        echo "===== DadDeals worker end: $(timestamp) status=1 ====="
        exit 1
    fi

    if [ ! -x "${PYTHON_BIN}" ]; then
        echo "ERROR: Python was not found at ${PYTHON_BIN}"
        echo "Run the setup steps first, or edit PROJECT_DIR in scripts/run_worker.sh."
        echo "===== DadDeals worker end: $(timestamp) status=1 ====="
        exit 1
    fi

    cd "${PROJECT_DIR}" || {
        echo "ERROR: Could not cd into ${PROJECT_DIR}"
        echo "===== DadDeals worker end: $(timestamp) status=1 ====="
        exit 1
    }

    "${PYTHON_BIN}" worker.py --run --send-alerts
    status=$?

    echo "===== DadDeals worker end: $(timestamp) status=${status} ====="
    exit "${status}"
} >> "${LOG_FILE}" 2>&1
