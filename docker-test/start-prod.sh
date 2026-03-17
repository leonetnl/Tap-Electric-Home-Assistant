#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p ha-config
cp configuration.yaml ha-config/configuration.yaml

docker compose up -d

echo "Home Assistant testomgeving gestart zonder mock API."
echo "Open: http://localhost:8123"

