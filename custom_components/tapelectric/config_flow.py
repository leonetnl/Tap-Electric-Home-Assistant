"""Config flow for the Tap Electric integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import aiohttp_client, selector

from .api import TapElectricApiClient
from .const import CONF_API_KEY, CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN
from .exceptions import (
    TapElectricApiAuthenticationError,
    TapElectricApiConnectionError,
    TapElectricApiError,
    TapElectricApiRateLimitError,
)

_LOGGER = logging.getLogger(__name__)


class TapElectricConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tap Electric."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            normalized_input = {
                CONF_API_KEY: user_input[CONF_API_KEY].strip(),
                CONF_BASE_URL: user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL).strip(),
            }

            try:
                info = await _validate_input(self.hass, normalized_input)
            except TapElectricApiAuthenticationError:
                errors["base"] = "invalid_auth"
            except (TapElectricApiConnectionError, TapElectricApiRateLimitError):
                errors["base"] = "cannot_connect"
            except TapElectricApiError:
                _LOGGER.exception("Unexpected Tap Electric API error during config flow")
                errors["base"] = "unknown"
            except Exception:  # pragma: no cover - defensive fallback
                _LOGGER.exception("Unexpected error during Tap Electric config flow")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["account_key"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["title"],
                    data=normalized_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(user_input),
            errors=errors,
        )


async def _validate_input(
    hass,
    data: dict[str, Any],
) -> dict[str, str]:
    """Validate the user input."""
    client = TapElectricApiClient(
        session=aiohttp_client.async_get_clientsession(hass),
        api_key=data[CONF_API_KEY],
        base_url=data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )
    return await client.async_validate_api_key()


def _build_schema(user_input: dict[str, Any] | None) -> vol.Schema:
    """Build the config flow schema."""
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_API_KEY,
                default=user_input.get(CONF_API_KEY, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                )
            ),
            vol.Optional(
                CONF_BASE_URL,
                default=user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            ): str,
        }
    )

