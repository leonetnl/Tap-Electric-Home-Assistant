"""Constants for the Tap Electric integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "tapelectric"
NAME = "Tap Electric"
MANUFACTURER = "Tap Electric"

CONF_API_KEY = "api_key"
CONF_BASE_URL = "base_url"

DEFAULT_BASE_URL = "https://api.tapelectric.app"
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_TIMEOUT = 15
MAX_API_RETRIES = 3
MIN_REQUEST_INTERVAL_SECONDS = 0.25
MAX_PARALLEL_STATUS_REQUESTS = 4
HISTORY_STORAGE_VERSION = 1
MAX_IMPORTED_SESSION_IDS = 500

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]
