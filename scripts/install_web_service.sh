#!/usr/bin/env bash

# Install the DadDeals website as a Raspberry Pi systemd service.
#
# This script copies deployment/daddeals.service.example to:
#   /etc/systemd/system/daddeals.service
#
# It uses sudo for system folders and systemctl commands. If your project is
# not in /home/pi/DadDeals, edit deployment/daddeals.service.example first.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
SERVICE_SOURCE="${PROJECT_DIR}/deployment/daddeals.service.example"
SERVICE_TARGET="/etc/systemd/system/daddeals.service"
EXPECTED_PROJECT_DIR="/home/pi/DadDeals"

echo "DadDeals web service installer"
echo "Project directory: ${PROJECT_DIR}"
echo "Service source: ${SERVICE_SOURCE}"
echo "Service target: ${SERVICE_TARGET}"
echo

if [ ! -f "${SERVICE_SOURCE}" ]; then
    echo "ERROR: Service file was not found: ${SERVICE_SOURCE}"
    exit 1
fi

if [ "${PROJECT_DIR}" != "${EXPECTED_PROJECT_DIR}" ]; then
    echo "WARNING: This project is not at ${EXPECTED_PROJECT_DIR}."
    echo "Edit deployment/daddeals.service.example before installing if your Pi path is different."
    echo
    printf "Continue anyway? [y/N] "
    read -r path_answer
    case "${path_answer}" in
        y|Y|yes|YES)
            echo "Continuing with the service file as written."
            ;;
        *)
            echo "Cancelled. Edit the service file, then run this script again."
            exit 0
            ;;
    esac
    echo
fi

echo "This script needs sudo because systemd service files live under /etc."
echo

if sudo test -f "${SERVICE_TARGET}"; then
    echo "A DadDeals service already exists at ${SERVICE_TARGET}."
    printf "Overwrite it? [y/N] "
    read -r answer
    case "${answer}" in
        y|Y|yes|YES)
            echo "Overwriting existing service file."
            ;;
        *)
            echo "Cancelled. Existing service file was left unchanged."
            exit 0
            ;;
    esac
fi

sudo cp "${SERVICE_SOURCE}" "${SERVICE_TARGET}"
sudo systemctl daemon-reload
sudo systemctl enable daddeals.service
sudo systemctl restart daddeals.service

echo
echo "DadDeals web service installed and restarted."
echo
echo "Useful commands:"
echo "  sudo systemctl status daddeals.service"
echo "  sudo systemctl restart daddeals.service"
echo "  sudo systemctl stop daddeals.service"
echo "  journalctl -u daddeals.service -n 80 --no-pager"
