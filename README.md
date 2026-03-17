# Tap Electric Home Assistant Integration

Tap Electric is a custom Home Assistant integration for reading charger and session data from the Tap Electric API.

This repository is structured for installation through HACS and follows modern Home Assistant integration patterns:

- Config flow based setup
- Async I/O with `aiohttp`
- `DataUpdateCoordinator` for efficient polling
- Multiple chargers per account
- Per-charger devices and entities
- Defensive parsing for incomplete or evolving API responses

## Features

- API key authentication
- Optional configurable API base URL
- Connectivity test during config flow setup
- Discovery of multiple chargers on the same Tap Electric account
- Per-charger sensors for:
  - Load status
  - Current power draw
  - Current session energy
  - Historical total energy
  - Session start time
  - Session duration
  - Session cost
  - Online/offline status
  - Connector status
- Per-charger binary sensors for:
  - Charging
  - Occupied
- Raw API fragments exposed in `extra_state_attributes` for easier debugging

## Installation via HACS

1. Push this repository to GitHub.
2. In Home Assistant, open HACS.
3. Go to `Integrations`.
4. Open the menu in the top-right corner and choose `Custom repositories`.
5. Add your GitHub repository URL.
6. Select repository category `Integration`.
7. Search for `Tap Electric` in HACS and install it.
8. Restart Home Assistant.
9. Go to `Settings` -> `Devices & services` -> `Add integration`.
10. Search for `Tap Electric`.

## Manual Installation

1. Copy `custom_components/tapelectric` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Go to `Settings` -> `Devices & services`.
4. Add the `Tap Electric` integration.

## Configuration

The config flow asks for:

- `API key`
- `Base URL` (optional, defaults to the public Tap Electric API URL placeholder)

The integration validates the API key during setup by performing a test API request.

## Example entities

Depending on the charger names and API response fields, you can expect entities similar to:

- `sensor.tap_electric_home_charger_load_status`
- `sensor.tap_electric_home_charger_current_power`
- `sensor.tap_electric_home_charger_current_session_energy`
- `sensor.tap_electric_home_charger_total_energy`
- `sensor.tap_electric_home_charger_session_start`
- `sensor.tap_electric_home_charger_session_duration`
- `sensor.tap_electric_home_charger_session_cost`
- `sensor.tap_electric_home_charger_online_status`
- `sensor.tap_electric_home_charger_connector_status`
- `binary_sensor.tap_electric_home_charger_charging`
- `binary_sensor.tap_electric_home_charger_occupied`

## Troubleshooting

### Config flow reports authentication failed

- Verify that the API key is correct.
- Verify that the API key has permission to read chargers and sessions.
- If Tap Electric uses a different environment or regional API host, set the correct base URL during setup.

### Entities are created but some values are `unknown`

This integration is built defensively because the exact public Tap Electric response format may differ by endpoint or API environment.

Check `extra_state_attributes` on the affected entity. Raw API fragments are exposed there so you can inspect the returned field names and map them more precisely.

### Temporary API errors

The API client retries temporary failures such as:

- timeouts
- `429 Too Many Requests`
- `5xx` server errors

If the API remains unavailable, the coordinator update will fail temporarily and Home Assistant will mark the entities unavailable until the next successful refresh.

## Known limitations

- Exact Tap Electric API endpoints and field names may still need confirmation.
- New chargers added after setup may require a reload of the integration if Home Assistant has not yet created the matching entities.
- This integration is read-only. It does not start or stop charging sessions.
- Session and cost sensors depend on the API exposing those fields.

## Development notes

### Endpoint placeholders

The API client in `custom_components/tapelectric/api.py` contains explicit placeholder endpoint candidate lists and `TODO: confirm exact Tap Electric endpoint/response` comments.

Update these first if you have official Tap Electric API documentation.

### Extending with new sensors

Add a new `TapElectricSensorEntityDescription` in `custom_components/tapelectric/sensor.py`, pointing it at a normalized field from the coordinator snapshot.

Minimal example:

```python
TapElectricSensorEntityDescription(
    key="voltage",
    translation_key="voltage",
    icon="mdi:sine-wave",
    native_unit_of_measurement="V",
    value_fn=lambda snapshot: snapshot.get("voltage"),
)
```

If the value is not already normalized, add the parsing logic in `custom_components/tapelectric/coordinator.py`.

### API mapping workflow

1. Confirm the real Tap Electric endpoints.
2. Update the candidate endpoint lists in the API client.
3. Inspect raw entity attributes in Home Assistant.
4. Tighten the parsing helpers in the coordinator for the confirmed field names.
