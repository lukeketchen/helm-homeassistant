"""Diagnostics for the Helm integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from . import HelmConfigEntry

TO_REDACT = {CONF_API_TOKEN, "name", "title", "details", "url"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HelmConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with household content redacted."""
    runtime = entry.runtime_data
    planning = runtime.planning
    shopping = runtime.shopping

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "abilities": sorted(runtime.abilities),
        "planning": {
            "last_update_success": planning.last_update_success if planning else None,
            "timezone": planning.timezone_name if planning else None,
            "window": {
                "from": planning.window_start.isoformat()
                if planning and planning.window_start
                else None,
                "to": planning.window_end.isoformat()
                if planning and planning.window_end
                else None,
            },
            "counts": {
                kind: len(items) for kind, items in (planning.data or {}).items()
            }
            if planning
            else {},
            "sample": async_redact_data(
                (planning.all_occurrences() or [{}])[0] if planning else {}, TO_REDACT
            ),
        },
        "shopping": {
            "last_update_success": shopping.last_update_success if shopping else None,
            "count": len(shopping.data or []) if shopping else 0,
            "sample": async_redact_data(
                (shopping.data or [{}])[0] if shopping else {}, TO_REDACT
            ),
        },
    }
