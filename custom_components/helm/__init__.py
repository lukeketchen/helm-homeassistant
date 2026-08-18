"""The Helm integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from pathlib import Path
from typing import Any

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_TOKEN, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.loader import async_get_loaded_integration
from homeassistant.util import dt as dt_util

from .api import HelmClient
from .const import (
    ABILITY_PLANNING_READ,
    ABILITY_SHOPPING_READ,
    ABILITY_SHOPPING_WRITE,
    CONF_ABILITIES,
    CONF_BASE_URL,
    CONF_CREDENTIAL,
    CONF_TEAM,
    CONF_TIMEZONE,
    CONF_USER,
    DOMAIN,
    EXPIRY_WARNING_DAYS,
    ISSUE_TOKEN_EXPIRING,
)
from .coordinator import HelmPlanningCoordinator, HelmShoppingCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

EXPIRY_CHECK_INTERVAL = timedelta(hours=12)


@dataclass
class HelmRuntimeData:
    """Everything the platforms need, hung off the config entry."""

    client: HelmClient
    abilities: set[str] = field(default_factory=set)
    planning: HelmPlanningCoordinator | None = None
    shopping: HelmShoppingCoordinator | None = None
    credential: dict[str, Any] = field(default_factory=dict)
    user: dict[str, Any] = field(default_factory=dict)
    team: dict[str, Any] = field(default_factory=dict)

    @property
    def expires_at(self) -> datetime | None:
        """When this credential lapses, if it ever does."""
        raw = self.credential.get("expires_at")
        return dt_util.parse_datetime(raw) if isinstance(raw, str) else None


HelmConfigEntry = ConfigEntry[HelmRuntimeData]


def _platforms_for(abilities: set[str]) -> list[Platform]:
    """Return only the platforms the token's abilities can actually feed."""
    platforms: list[Platform] = []
    if ABILITY_PLANNING_READ in abilities:
        platforms.append(Platform.CALENDAR)
    if ABILITY_SHOPPING_READ in abilities:
        platforms.append(Platform.TODO)
    if abilities:
        platforms.append(Platform.SENSOR)
    return platforms


CARD_URL = "/helm_static/helm-shopping-card.js"
CARD_FILENAME = "helm-shopping-card.js"


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the shopping card and add it to the frontend.

    Registering it here means the user never has to add a Lovelace resource by
    hand, and HACS never needs a second repository for the frontend half.
    """
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                "/helm_static", str(Path(__file__).parent / "www"), cache_headers=True
            )
        ]
    )
    # The version query string busts the browser cache on upgrade.
    version = async_get_loaded_integration(hass, DOMAIN).version
    frontend.add_extra_js_url(hass, f"{CARD_URL}?v={version}")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the services and the frontend card."""
    async_setup_services(hass)
    try:
        await _async_register_card(hass)
    except Exception:  # the card is optional; entities must load regardless
        _LOGGER.exception("Could not register the Helm shopping card")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HelmConfigEntry) -> bool:
    """Set up Helm from a config entry."""
    client = HelmClient(
        async_get_clientsession(hass),
        entry.data[CONF_BASE_URL],
        entry.data[CONF_API_TOKEN],
    )

    abilities = set(entry.data.get(CONF_ABILITIES, []))
    runtime = HelmRuntimeData(
        client=client,
        abilities=abilities,
        credential=entry.data.get(CONF_CREDENTIAL) or {},
        user=entry.data.get(CONF_USER) or {},
        team=entry.data.get(CONF_TEAM) or {},
    )

    if ABILITY_PLANNING_READ in abilities:
        runtime.planning = HelmPlanningCoordinator(hass, entry, client)
        # /me already told us the household timezone, so even the first poll
        # resolves "today" correctly.
        await runtime.planning.async_set_timezone(entry.data.get(CONF_TIMEZONE))
        await runtime.planning.async_config_entry_first_refresh()

    if ABILITY_SHOPPING_READ in abilities:
        runtime.shopping = HelmShoppingCoordinator(hass, entry, client)
        await runtime.shopping.async_config_entry_first_refresh()

    entry.runtime_data = runtime

    platforms = _platforms_for(abilities)
    if not platforms:
        _LOGGER.warning(
            "The Helm token has none of the %s, %s or %s abilities, so no entities "
            "were created. Reissue it with the abilities you need",
            ABILITY_PLANNING_READ,
            ABILITY_SHOPPING_READ,
            ABILITY_SHOPPING_WRITE,
        )
    else:
        await hass.config_entries.async_forward_entry_setups(entry, platforms)

    _async_check_expiry(hass, entry)
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            lambda _now: _async_check_expiry(hass, entry),
            EXPIRY_CHECK_INTERVAL,
        )
    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


@callback
def _async_check_expiry(hass: HomeAssistant, entry: HelmConfigEntry) -> None:
    """Raise a repair when the credential is close to lapsing.

    The expiry is fixed when the credential is issued, so this only re-reads
    what /me already told us — it costs no API requests.
    """
    issue_id = f"{ISSUE_TOKEN_EXPIRING}_{entry.entry_id}"
    expires_at = entry.runtime_data.expires_at

    if expires_at is None or expires_at - dt_util.utcnow() > timedelta(
        days=EXPIRY_WARNING_DAYS
    ):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_TOKEN_EXPIRING,
        translation_placeholders={
            "name": entry.title,
            "credential": (
                entry.runtime_data.credential.get("name") or "this credential"
            ),
            "expires_at": expires_at.isoformat(timespec="minutes"),
        },
    )


async def async_unload_entry(hass: HomeAssistant, entry: HelmConfigEntry) -> bool:
    """Unload a config entry."""
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_TOKEN_EXPIRING}_{entry.entry_id}")
    platforms = _platforms_for(entry.runtime_data.abilities)
    if not platforms:
        return True
    return await hass.config_entries.async_unload_platforms(entry, platforms)


async def async_reload_entry(hass: HomeAssistant, entry: HelmConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
