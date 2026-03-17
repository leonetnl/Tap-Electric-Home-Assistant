"""Small mock Tap Electric API for local integration testing."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 8080
EXPECTED_API_KEY = os.environ.get("MOCK_API_KEY", "tap-test-key")

ACCOUNT = {
    "id": "mock-account-1",
    "name": "Mock Tap Electric Account",
}

CHARGERS = {
    "chargers": [
        {
            "id": "charger-1",
            "name": "Oprit",
            "model": "Tap Charger Pro",
            "serial_number": "TAP-001",
            "location_name": "Thuis",
            "firmware_version": "1.2.3",
        },
        {
            "id": "charger-2",
            "name": "Garage",
            "model": "Tap Charger Mini",
            "serial_number": "TAP-002",
            "location_name": "Thuis",
            "firmware_version": "1.2.1",
        },
    ]
}

CHARGER_STATUSES = {
    "charger-1": {
        "id": "charger-1",
        "status": "charging",
        "is_online": True,
        "current_power_kw": 7.4,
        "connectors": [
            {
                "id": "connector-1",
                "status": "occupied",
            }
        ],
    },
    "charger-2": {
        "id": "charger-2",
        "status": "available",
        "is_online": True,
        "current_power_kw": 0.0,
        "connectors": [
            {
                "id": "connector-2",
                "status": "available",
            }
        ],
    },
}

ACTIVE_SESSIONS = {
    "active_sessions": [
        {
            "id": "session-active-1",
            "charger_id": "charger-1",
            "start_time": "2026-03-17T17:15:00Z",
            "session_energy_kwh": 11.8,
            "cost": 4.72,
            "currency": "EUR",
            "status": "charging",
        }
    ]
}

SESSIONS = {
    "sessions": [
        {
            "id": "session-active-1",
            "charger_id": "charger-1",
            "start_time": "2026-03-17T17:15:00Z",
            "energy_delivered_kwh": 11.8,
            "total_cost": 4.72,
            "currency": "EUR",
            "status": "charging",
        },
        {
            "id": "session-historic-1",
            "charger_id": "charger-1",
            "start_time": "2026-03-16T19:00:00Z",
            "energy_delivered_kwh": 9.6,
            "total_cost": 3.95,
            "currency": "EUR",
            "status": "completed",
        },
        {
            "id": "session-historic-2",
            "charger_id": "charger-2",
            "start_time": "2026-03-15T18:00:00Z",
            "energy_delivered_kwh": 6.2,
            "total_cost": 2.41,
            "currency": "EUR",
            "status": "completed",
        },
    ]
}


def _is_authorized(headers) -> bool:
    """Check whether the request uses the configured mock API key."""
    api_key = headers.get("X-API-Key", "")
    authorization = headers.get("Authorization", "")
    bearer = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    return api_key == EXPECTED_API_KEY or bearer == EXPECTED_API_KEY


class MockApiHandler(BaseHTTPRequestHandler):
    """Serve a minimal Tap Electric compatible mock API."""

    server_version = "TapElectricMock/1.0"

    def do_GET(self) -> None:  # noqa: N802
        """Handle GET requests."""
        if not _is_authorized(self.headers):
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "invalid_auth", "message": "Invalid mock API key"},
            )
            return

        routes = {
            "/v1/me": ACCOUNT,
            "/api/v1/me": ACCOUNT,
            "/v1/account": ACCOUNT,
            "/api/v1/account": ACCOUNT,
            "/v1/chargers": CHARGERS,
            "/api/v1/chargers": CHARGERS,
            "/v1/charge-points": CHARGERS,
            "/api/v1/charge-points": CHARGERS,
            "/v1/sessions/active": ACTIVE_SESSIONS,
            "/api/v1/sessions/active": ACTIVE_SESSIONS,
            "/v1/charging-sessions/active": ACTIVE_SESSIONS,
            "/api/v1/charging-sessions/active": ACTIVE_SESSIONS,
            "/v1/sessions": SESSIONS,
            "/api/v1/sessions": SESSIONS,
            "/v1/charging-sessions": SESSIONS,
            "/api/v1/charging-sessions": SESSIONS,
        }

        if self.path in routes:
            self._send_json(HTTPStatus.OK, routes[self.path])
            return

        for charger_id, payload in CHARGER_STATUSES.items():
            status_paths = {
                f"/v1/chargers/{charger_id}": payload,
                f"/api/v1/chargers/{charger_id}": payload,
                f"/v1/chargers/{charger_id}/status": payload,
                f"/api/v1/chargers/{charger_id}/status": payload,
                f"/v1/charge-points/{charger_id}": payload,
                f"/api/v1/charge-points/{charger_id}": payload,
            }
            if self.path in status_paths:
                self._send_json(HTTPStatus.OK, status_paths[self.path])
                return

        self._send_json(
            HTTPStatus.NOT_FOUND,
            {"error": "not_found", "path": self.path},
        )

    def log_message(self, format: str, *args) -> None:
        """Log to stdout in a compact format."""
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args))

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        """Send a JSON response."""
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    """Start the mock HTTP server."""
    server = ThreadingHTTPServer((HOST, PORT), MockApiHandler)
    print(f"Tap Electric mock API listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
