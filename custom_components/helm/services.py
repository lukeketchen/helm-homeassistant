"""Integration-level services for Helm."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
import voluptuous as vol

from .api import HelmError
from .const import (
    ABILITY_PLANNING_READ,
    ABILITY_SHOPPING_WRITE,
    ATTR_CATEGORY_ID,
    ATTR_COMPLETED,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_ENTITY_ID,
    ATTR_FROM,
    ATTR_IDEMPOTENCY_KEY,
    ATTR_ITEM_ID,
    ATTR_NAME,
    ATTR_QTY,
    ATTR_SORT_ORDER,
    ATTR_TO,
    ATTR_TYPES,
    ATTR_URL,
    ATTR_VISIBLE_TO_EXTENDED,
    DOMAIN,
    PLANNING_TYPES,
    SERVICE_ADD_SHOPPING_ITEM,
    SERVICE_DELETE_SHOPPING_ITEM,
    SERVICE_GET_PLANNING,
    SERVICE_UPDATE_SHOPPING_ITEM,
)

# Every service can be aimed either at a config entry or at one of its
# entities, so the shopping card can just pass the entity it is configured with.
ENTRY_SCHEMA = {
    vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
}


def _with_target(schema: dict) -> vol.All:
    """Require exactly one of config_entry_id or entity_id."""
    return vol.All(
        vol.Schema(schema),
        cv.has_at_least_one_key(ATTR_CONFIG_ENTRY_ID, ATTR_ENTITY_ID),
    )


ADD_ITEM_SCHEMA = _with_target(
    {
        **ENTRY_SCHEMA,
        vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(min=1, max=255)),
        vol.Optional(ATTR_QTY): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(ATTR_CATEGORY_ID): vol.Coerce(int),
        vol.Optional(ATTR_URL): vol.All(cv.string, vol.Length(max=2048)),
        vol.Optional(ATTR_VISIBLE_TO_EXTENDED): cv.boolean,
        vol.Optional(ATTR_IDEMPOTENCY_KEY): vol.All(
            cv.string, vol.Length(min=1, max=255)
        ),
    }
)

UPDATE_ITEM_SCHEMA = _with_target(
    {
        **ENTRY_SCHEMA,
        vol.Required(ATTR_ITEM_ID): vol.Coerce(int),
        vol.Optional(ATTR_NAME): vol.All(cv.string, vol.Length(min=1, max=255)),
        vol.Optional(ATTR_QTY): vol.Any(
            None, vol.All(vol.Coerce(int), vol.Range(min=1))
        ),
        vol.Optional(ATTR_CATEGORY_ID): vol.Any(None, vol.Coerce(int)),
        vol.Optional(ATTR_URL): vol.Any(None, vol.All(cv.string, vol.Length(max=2048))),
        vol.Optional(ATTR_COMPLETED): cv.boolean,
        vol.Optional(ATTR_SORT_ORDER): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(ATTR_VISIBLE_TO_EXTENDED): cv.boolean,
    }
)

DELETE_ITEM_SCHEMA = _with_target(
    {**ENTRY_SCHEMA, vol.Required(ATTR_ITEM_ID): vol.Coerce(int)}
)

GET_PLANNING_SCHEMA = _with_target(
    {
        **ENTRY_SCHEMA,
        vol.Optional(ATTR_FROM): cv.date,
        vol.Optional(ATTR_TO): cv.date,
        vol.Optional(ATTR_TYPES): vol.All(
            cv.ensure_list, [vol.In(PLANNING_TYPES)], vol.Length(min=1)
        ),
    }
)


def _entry_id_from_entity(hass: HomeAssistant, entity_id: str) -> str:
    """Map one of our entities back to the config entry behind it."""
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None:
        raise ServiceValidationError(f"No such entity: {entity_id}")
    if entity.platform != DOMAIN or entity.config_entry_id is None:
        raise ServiceValidationError(f"{entity_id} is not a Helm entity")
    return entity.config_entry_id


def _runtime(hass: HomeAssistant, call: ServiceCall):
    """Resolve the loaded config entry a service call is aimed at."""
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID) or _entry_id_from_entity(
        hass, call.data[ATTR_ENTITY_ID]
    )
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(f"No Helm config entry with ID {entry_id}")
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(f"The Helm config entry {entry_id} is not loaded")
    return entry.runtime_data


def _require(runtime, ability: str) -> None:
    """Fail early when the token cannot do what the service asks."""
    if ability not in runtime.abilities:
        raise ServiceValidationError(
            f"The Helm token does not have the '{ability}' ability"
        )


def _item_fields(call: ServiceCall) -> dict[str, Any]:
    """Pull the shopping item fields out of a service call."""
    keys = (
        ATTR_NAME,
        ATTR_QTY,
        ATTR_CATEGORY_ID,
        ATTR_URL,
        ATTR_COMPLETED,
        ATTR_SORT_ORDER,
        ATTR_VISIBLE_TO_EXTENDED,
    )
    return {key: call.data[key] for key in keys if key in call.data}


async def _async_add_item(call: ServiceCall) -> ServiceResponse:
    """Add a shopping list item with the fields the to-do UI cannot reach."""
    runtime = _runtime(call.hass, call)
    _require(runtime, ABILITY_SHOPPING_WRITE)
    try:
        item = await runtime.client.async_create_shopping_item(
            _item_fields(call), idempotency_key=call.data.get(ATTR_IDEMPOTENCY_KEY)
        )
    except HelmError as err:
        raise HomeAssistantError(err.message) from err
    if runtime.shopping is not None:
        await runtime.shopping.async_request_refresh()
    return {"item": item}


async def _async_update_item(call: ServiceCall) -> ServiceResponse:
    """Update a shopping list item by ID."""
    runtime = _runtime(call.hass, call)
    _require(runtime, ABILITY_SHOPPING_WRITE)
    fields = _item_fields(call)
    if not fields:
        raise ServiceValidationError("Provide at least one field to update")
    try:
        item = await runtime.client.async_update_shopping_item(
            call.data[ATTR_ITEM_ID], fields
        )
    except HelmError as err:
        raise HomeAssistantError(err.message) from err
    if runtime.shopping is not None:
        await runtime.shopping.async_request_refresh()
    return {"item": item}


async def _async_delete_item(call: ServiceCall) -> None:
    """Delete a shopping list item by ID."""
    runtime = _runtime(call.hass, call)
    _require(runtime, ABILITY_SHOPPING_WRITE)
    try:
        await runtime.client.async_delete_shopping_item(call.data[ATTR_ITEM_ID])
    except HelmError as err:
        raise HomeAssistantError(err.message) from err
    if runtime.shopping is not None:
        await runtime.shopping.async_request_refresh()


async def _async_get_planning(call: ServiceCall) -> ServiceResponse:
    """Return planning occurrences for an arbitrary range."""
    runtime = _runtime(call.hass, call)
    _require(runtime, ABILITY_PLANNING_READ)
    coordinator = runtime.planning
    if coordinator is None:
        raise ServiceValidationError("Planning data is not available on this entry")

    start: date = call.data.get(ATTR_FROM, coordinator.today())
    end: date = call.data.get(ATTR_TO, start)
    if end < start:
        raise ServiceValidationError("'to' must be on or after 'from'")

    occurrences = await coordinator.async_fetch_range(
        start, end, call.data.get(ATTR_TYPES)
    )
    return {
        "occurrences": occurrences,
        "count": len(occurrences),
        "meta": {
            "timezone": coordinator.timezone_name,
            "from": start.isoformat(),
            "to": end.isoformat(),
        },
    }


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Helm services once, at integration setup."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_SHOPPING_ITEM,
        _async_add_item,
        schema=ADD_ITEM_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_SHOPPING_ITEM,
        _async_update_item,
        schema=UPDATE_ITEM_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_SHOPPING_ITEM,
        _async_delete_item,
        schema=DELETE_ITEM_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PLANNING,
        _async_get_planning,
        schema=GET_PLANNING_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
