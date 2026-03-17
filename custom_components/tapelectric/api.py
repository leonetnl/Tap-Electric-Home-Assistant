"""Async Tap Electric API client."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import logging
from time import monotonic
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError, ClientResponse, ClientSession

from .const import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    MAX_API_RETRIES,
    MIN_REQUEST_INTERVAL_SECONDS,
    NAME,
)
from .exceptions import (
    TapElectricApiAuthenticationError,
    TapElectricApiConnectionError,
    TapElectricApiError,
    TapElectricApiRateLimitError,
    TapElectricEndpointNotFoundError,
)

_LOGGER = logging.getLogger(__name__)

# TODO: confirm exact Tap Electric endpoint/response.
VALIDATION_ENDPOINT_CANDIDATES: tuple[str, ...] = (
    "/v1/me",
    "/api/v1/me",
    "/v1/account",
    "/api/v1/account",
    "/v1/chargers",
    "/api/v1/chargers",
)

# TODO: confirm exact Tap Electric endpoint/response.
CHARGERS_ENDPOINT_CANDIDATES: tuple[str, ...] = (
    "/v1/chargers",
    "/api/v1/chargers",
    "/v1/charge-points",
    "/api/v1/charge-points",
)

# TODO: confirm exact Tap Electric endpoint/response.
CHARGER_STATUS_ENDPOINT_CANDIDATES: tuple[str, ...] = (
    "/v1/chargers/{charger_id}",
    "/api/v1/chargers/{charger_id}",
    "/v1/chargers/{charger_id}/status",
    "/api/v1/chargers/{charger_id}/status",
    "/v1/charge-points/{charger_id}",
    "/api/v1/charge-points/{charger_id}",
)

# TODO: confirm exact Tap Electric endpoint/response.
ACTIVE_SESSIONS_ENDPOINT_CANDIDATES: tuple[str, ...] = (
    "/v1/sessions/active",
    "/api/v1/sessions/active",
    "/v1/charging-sessions/active",
    "/api/v1/charging-sessions/active",
)

# TODO: confirm exact Tap Electric endpoint/response.
SESSIONS_ENDPOINT_CANDIDATES: tuple[str, ...] = (
    "/api/v1/charger-sessions",
    "/v1/charger-sessions",
    "/v1/sessions",
    "/api/v1/sessions",
    "/v1/charging-sessions",
    "/api/v1/charging-sessions",
)


class TapElectricApiClient:
    """Small async API client with retry and placeholder endpoint support."""

    def __init__(
        self,
        session: ClientSession,
        api_key: str,
        base_url: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._api_key = api_key.strip()
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout = timeout
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0

    @property
    def base_url(self) -> str:
        """Return the configured base URL."""
        return self._base_url

    async def async_validate_api_key(self) -> dict[str, str]:
        """Validate credentials and return metadata for the config flow."""
        payload = await self._async_request_candidates(
            "GET",
            VALIDATION_ENDPOINT_CANDIDATES,
            purpose="validate API key",
        )

        hostname = urlparse(self._base_url).hostname or self._base_url
        account_name = self._extract_name(payload) or f"{NAME} ({hostname})"

        return {
            "account_key": self._build_account_key(),
            "title": account_name,
        }

    async def async_get_chargers(self) -> list[dict[str, Any]]:
        """Return the charger list for the account."""
        payload = await self._async_request_candidates(
            "GET",
            CHARGERS_ENDPOINT_CANDIDATES,
            purpose="fetch chargers",
        )
        chargers = self._extract_list(payload, ("chargers", "charge_points", "items", "data", "results"))
        if not chargers:
            _LOGGER.warning(
                "Tap Electric returned no chargers. "
                "TODO: confirm exact charger list endpoint/response."
            )
        return chargers

    async def async_get_charger_status(self, charger_id: str) -> dict[str, Any]:
        """Return status data for a single charger."""
        payload = await self._async_request_candidates(
            "GET",
            tuple(
                endpoint.format(charger_id=charger_id)
                for endpoint in CHARGER_STATUS_ENDPOINT_CANDIDATES
            ),
            purpose=f"fetch charger status for {charger_id}",
            optional=True,
            default={},
        )
        return payload if isinstance(payload, dict) else {}

    async def async_get_active_sessions(self) -> list[dict[str, Any]]:
        """Return active charging sessions if available."""
        payload = await self._async_request_candidates(
            "GET",
            ACTIVE_SESSIONS_ENDPOINT_CANDIDATES,
            purpose="fetch active sessions",
            optional=True,
            default=[],
        )
        return self._extract_list(
            payload,
            ("active_sessions", "sessions", "charging_sessions", "items", "data", "results"),
        )

    async def async_get_sessions(self) -> list[dict[str, Any]]:
        """Return historical charging sessions if available."""
        payload = await self._async_request_candidates(
            "GET",
            SESSIONS_ENDPOINT_CANDIDATES,
            purpose="fetch sessions",
            optional=True,
            default=[],
        )
        sessions = self._extract_list(
            payload,
            (
                "sessions",
                "charging_sessions",
                "charger_sessions",
                "chargingSessions",
                "chargerSessions",
                "items",
                "data",
                "results",
                "content",
            ),
        )
        if not sessions:
            if isinstance(payload, dict):
                _LOGGER.debug(
                    "Tap Electric sessions response contained no recognized session list. Top-level keys: %s",
                    sorted(payload.keys()),
                )
            elif isinstance(payload, list):
                _LOGGER.debug(
                    "Tap Electric sessions response was a list but contained no recognized session dict entries"
                )
            else:
                _LOGGER.debug(
                    "Tap Electric sessions response contained no recognized session list. Payload type: %s",
                    type(payload).__name__,
                )
        return sessions

    async def _async_request_candidates(
        self,
        method: str,
        candidate_paths: tuple[str, ...],
        *,
        purpose: str,
        optional: bool = False,
        default: Any = None,
    ) -> Any:
        """Try multiple candidate endpoints until one works."""
        last_exception: Exception | None = None

        for path in candidate_paths:
            try:
                return await self._async_request(method, path, purpose=purpose)
            except TapElectricEndpointNotFoundError as err:
                last_exception = err
                _LOGGER.debug(
                    "Tap Electric endpoint candidate not found for %s: %s",
                    purpose,
                    path,
                )
            except TapElectricApiAuthenticationError:
                raise
            except TapElectricApiConnectionError:
                raise
            except TapElectricApiRateLimitError:
                raise
            except TapElectricApiError as err:
                last_exception = err
                _LOGGER.debug(
                    "Tap Electric endpoint candidate failed for %s: %s (%s)",
                    purpose,
                    path,
                    err,
                )

        if optional:
            _LOGGER.debug(
                "No compatible Tap Electric endpoint found for optional request: %s",
                purpose,
            )
            return default

        raise TapElectricApiError(
            f"Unable to complete request to {purpose}. "
            "TODO: confirm exact Tap Electric endpoint/response."
        ) from last_exception

    async def _async_request(self, method: str, path: str, *, purpose: str) -> Any:
        """Execute a single API request with retry and simple rate limiting."""
        url = f"{self._base_url}/{path.lstrip('/')}"

        for attempt in range(1, MAX_API_RETRIES + 1):
            await self._async_wait_for_rate_limit()

            try:
                _LOGGER.debug("Tap Electric API request (%s): %s", purpose, url)
                response = await self._session.request(
                    method,
                    url,
                    headers=self._build_headers(),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError as err:
                if attempt >= MAX_API_RETRIES:
                    raise TapElectricApiConnectionError(
                        f"Timed out while trying to {purpose}"
                    ) from err
                await asyncio.sleep(attempt)
                continue
            except ClientError as err:
                if attempt >= MAX_API_RETRIES:
                    raise TapElectricApiConnectionError(
                        f"Network error while trying to {purpose}"
                    ) from err
                await asyncio.sleep(attempt)
                continue

            try:
                if response.status in (401, 403):
                    raise TapElectricApiAuthenticationError("Invalid Tap Electric API key")

                if response.status == 404:
                    raise TapElectricEndpointNotFoundError(
                        f"Endpoint not found while trying to {purpose}"
                    )

                if response.status == 429:
                    retry_after = float(response.headers.get("Retry-After", "0") or 0)
                    if attempt >= MAX_API_RETRIES:
                        raise TapElectricApiRateLimitError(
                            "Tap Electric API rate limit exceeded"
                        )
                    await asyncio.sleep(max(retry_after, attempt))
                    continue

                if response.status >= 500:
                    if attempt >= MAX_API_RETRIES:
                        raise TapElectricApiConnectionError(
                            f"Tap Electric server error while trying to {purpose}"
                        )
                    await asyncio.sleep(attempt)
                    continue

                return await self._async_handle_response(response, purpose)
            finally:
                response.release()

        raise TapElectricApiError(f"Unexpected failure while trying to {purpose}")

    async def _async_handle_response(
        self,
        response: ClientResponse,
        purpose: str,
    ) -> Any:
        """Handle the HTTP response."""
        if response.status >= 400:
            body = await response.text()
            raise TapElectricApiError(
                f"Tap Electric API error while trying to {purpose}: "
                f"HTTP {response.status} - {body[:200]}"
            )

        if response.content_type and "json" not in response.content_type:
            body = await response.text()
            raise TapElectricApiError(
                f"Expected JSON from Tap Electric API while trying to {purpose}, "
                f"received {response.content_type}: {body[:200]}"
            )

        return await response.json(content_type=None)

    async def _async_wait_for_rate_limit(self) -> None:
        """Respect a small minimum interval between requests."""
        async with self._request_lock:
            elapsed = monotonic() - self._last_request_at
            if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
                await asyncio.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
            self._last_request_at = monotonic()

    def _build_headers(self) -> dict[str, str]:
        """Build the request headers."""
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "X-API-Key": self._api_key,
        }

    def _build_account_key(self) -> str:
        """Return a stable, non-secret identifier for the config entry."""
        digest = sha256(f"{self._base_url}|{self._api_key}".encode("utf-8")).hexdigest()
        return digest[:16]

    @staticmethod
    def _extract_list(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
        """Extract a list of dicts from a response payload."""
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if not isinstance(payload, dict):
            return []

        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                for nested_key in ("items", "results", "content", "data"):
                    nested_value = value.get(nested_key)
                    if isinstance(nested_value, list):
                        return [item for item in nested_value if isinstance(item, dict)]

        return []

    @staticmethod
    def _extract_name(payload: Any) -> str | None:
        """Extract a readable account name from the validation payload."""
        if isinstance(payload, dict):
            for key in ("name", "full_name", "display_name", "account_name", "company_name"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        if isinstance(payload, list) and payload:
            return NAME

        return None
