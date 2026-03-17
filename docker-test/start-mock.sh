#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p ha-config
cp configuration.yaml ha-config/configuration.yaml

docker compose --profile mock up -d

echo "Home Assistant testomgeving gestart met mock API."
echo "Open: http://localhost:8123"
echo "Mock API key: tap-test-key"
echo "Mock Base URL: http://mock-api:8080"

