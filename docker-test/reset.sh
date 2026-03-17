#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

docker compose down || true
rm -rf ha-config

echo "Docker testomgeving gestopt en ha-config verwijderd."

