# Docker testomgeving

Deze map bevat een losse Home Assistant testomgeving voor de Tap Electric integratie.

## Beschikbare testmodi

### 1. Testen tegen productie of een echte Tap Electric API

Start alleen Home Assistant:

```bash
cd docker-test
./start-prod.sh
```

Open daarna:

```text
http://localhost:8123
```

Voeg vervolgens de integratie toe in Home Assistant en gebruik:

- je echte API key
- de standaard base URL uit de integratie, of de echte Tap Electric base URL als die afwijkt

### 2. Testen tegen de lokale mock API

Start Home Assistant plus de mock API:

```bash
cd docker-test
./start-mock.sh
```

Open daarna:

```text
http://localhost:8123
```

Gebruik in de Tap Electric config flow:

- API key: `tap-test-key`
- Base URL: `http://mock-api:8080`

De mock API is ook vanaf je host bereikbaar op:

```text
http://localhost:8080
```

## Wat deze setup doet

- gebruikt een aparte Home Assistant instance
- mount de lokale integratie vanuit `../custom_components/tapelectric`
- schrijft Home Assistant testdata alleen naar `docker-test/ha-config`
- gebruikt `docker-test/configuration.yaml` als template voor `docker-test/ha-config/configuration.yaml`
- zet debug logging aan voor `custom_components.tapelectric`
- kan optioneel een lokale mock Tap Electric API starten

## Mock API gedrag

De mock API biedt voorbeelddata voor:

- accountvalidatie
- meerdere laadpalen
- laadpaalstatus
- actieve sessies
- historische sessies

Zo kun je testen of:

- de config flow werkt
- meerdere devices worden aangemaakt
- sensors en binary sensors verschijnen
- entity mapping logisch werkt

## Stoppen

```bash
cd docker-test
docker compose down
```

## Reset van de testomgeving

Voor een volledig schone testinstallatie:

```bash
cd docker-test
./reset.sh
```

## Bestanden

- `docker-compose.yml`: start Home Assistant en optioneel de mock API
- `configuration.yaml`: minimale Home Assistant configuratie met debug logging
- `mock-api/mock_api.py`: eenvoudige Tap Electric mock API
- `start-mock.sh`: start Home Assistant met mock API
- `start-prod.sh`: start Home Assistant zonder mock API
- `reset.sh`: stopt de testomgeving en verwijdert runtime-data
