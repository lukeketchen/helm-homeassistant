"""Config flow for the Helm integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import (
    HelmAuthError,
    HelmClient,
    HelmConnectionError,
    HelmError,
    HelmRateLimitError,
)
from .const import (
    CONF_ABILITIES,
    CONF_BASE_URL,
    CONF_CREDENTIAL,
    CONF_DAYS_AHEAD,
    CONF_DAYS_PAST,
    CONF_QTY_IN_SUMMARY,
    CONF_SHOW_PEOPLE,
    CONF_TEAM,
    CONF_TIMEZONE,
    CONF_UPDATE_INTERVAL,
    CONF_USER,
    DEFAULT_DAYS_AHEAD,
    DEFAULT_DAYS_PAST,
    DEFAULT_NAME,
    DEFAULT_QTY_IN_SUMMARY,
    DEFAULT_SHOW_PEOPLE,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_DAYS_AHEAD,
    MAX_DAYS_PAST,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    SHOW_PEOPLE_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


def _host_of(base_url: str) -> str:
    """Return the hostname of a base URL."""
    return (urlparse(base_url).hostname or base_url).lower()


def _unique_id(base_url: str, me: dict[str, Any]) -> str:
    """Identify a credential by server, team and user.

    Servers without /me fall back to the hostname alone, which allows only one
    entry per server.
    """
    host = _host_of(base_url)
    team = (me.get("team") or {}).get("id")
    user = (me.get("user") or {}).get("id")
    if team is None or user is None:
        return host
    return f"{host}-{team}-{user}"


def _title(me: dict[str, Any]) -> str:
    """Name the entry after the household, and the credential when known."""
    team = (me.get("team") or {}).get("name")
    credential = (me.get("credential") or {}).get("name")
    if team and credential:
        return f"{team} ({credential})"
    return team or credential or DEFAULT_NAME


class HelmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Helm config flow."""

    VERSION = 1

    async def _async_validate(
        self, base_url: str, token: str
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Check the credential and find out who it is and what it may do."""
        errors: dict[str, str] = {}

        if not token.startswith("helm_"):
            return None, {CONF_API_TOKEN: "invalid_token_format"}

        client = HelmClient(async_get_clientsession(self.hass), base_url, token)
        try:
            me = await client.async_identify(dt_util.now().date())
        except HelmAuthError:
            errors["base"] = "invalid_auth"
        except HelmConnectionError:
            errors["base"] = "cannot_connect"
        except HelmRateLimitError:
            errors["base"] = "rate_limited"
        except HelmError:
            _LOGGER.exception("Unexpected error validating the Helm credential")
            errors["base"] = "unknown"
        else:
            if not me.get("abilities"):
                return None, {"base": "no_abilities"}
            return me, {}

        return None, errors

    @staticmethod
    def _entry_data(base_url: str, token: str, me: dict[str, Any]) -> dict[str, Any]:
        """Build the stored entry data from a /me response."""
        return {
            CONF_BASE_URL: base_url,
            CONF_API_TOKEN: token,
            CONF_ABILITIES: sorted(me.get("abilities") or []),
            CONF_USER: me.get("user"),
            CONF_TEAM: me.get("team"),
            CONF_TIMEZONE: me.get("timezone"),
            CONF_CREDENTIAL: me.get("credential"),
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the base URL and token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")
            token = user_input[CONF_API_TOKEN].strip()

            me, errors = await self._async_validate(base_url, token)
            if me is not None:
                # /me identifies the user and team, so two household members can
                # each add their own credential against the same server.
                await self.async_set_unique_id(_unique_id(base_url, me))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=_title(me),
                    data=self._entry_data(base_url, token, me),
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start the reauth flow after a token is revoked or expires."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take a freshly issued token."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            base_url = entry.data[CONF_BASE_URL]
            token = user_input[CONF_API_TOKEN].strip()

            me, errors = await self._async_validate(base_url, token)
            if me is not None:
                return self.async_update_reload_and_abort(
                    entry, data_updates=self._entry_data(base_url, token, me)
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"base_url": entry.data.get(CONF_BASE_URL, "")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow."""
        return HelmOptionsFlow()


class HelmOptionsFlow(OptionsFlow):
    """Tune the polling window without re-entering the credential."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DAYS_AHEAD,
                    default=options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=MAX_DAYS_AHEAD, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_DAYS_PAST,
                    default=options.get(CONF_DAYS_PAST, DEFAULT_DAYS_PAST),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=MAX_DAYS_PAST, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_UPDATE_INTERVAL,
                        max=MAX_UPDATE_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Required(
                    CONF_QTY_IN_SUMMARY,
                    default=options.get(CONF_QTY_IN_SUMMARY, DEFAULT_QTY_IN_SUMMARY),
                ): bool,
                vol.Required(
                    CONF_SHOW_PEOPLE,
                    default=options.get(CONF_SHOW_PEOPLE, DEFAULT_SHOW_PEOPLE),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(SHOW_PEOPLE_OPTIONS),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_SHOW_PEOPLE,
                    )
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
