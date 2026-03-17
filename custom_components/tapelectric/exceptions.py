"""Exceptions for the Tap Electric integration."""

from __future__ import annotations


class TapElectricApiError(Exception):
    """Base exception for API errors."""


class TapElectricApiAuthenticationError(TapElectricApiError):
    """Raised when API authentication fails."""


class TapElectricApiConnectionError(TapElectricApiError):
    """Raised when the API cannot be reached."""


class TapElectricApiRateLimitError(TapElectricApiError):
    """Raised when the API rate limit is exceeded."""


class TapElectricEndpointNotFoundError(TapElectricApiError):
    """Raised when an API endpoint candidate does not exist."""

