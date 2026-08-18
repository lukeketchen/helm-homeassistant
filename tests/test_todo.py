"""Tests for writing to the Helm shopping list through the to-do platform."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import BASE_URL, setup_helm

ITEMS_URL = f"{BASE_URL}/shopping-list/items"
ENTITY = "todo.helm_shopping_list"


def last_call(aioclient_mock: AiohttpClientMocker, method: str):
    """Return the most recent call made with a given HTTP method.

    Writes are followed by a coordinator refresh, so the last call overall is
    a GET rather than the write under test.
    """
    for call in reversed(aioclient_mock.mock_calls):
        if call[0].lower() == method.lower():
            return call
    raise AssertionError(f"No {method.upper()} request was made")


def last_body(aioclient_mock: AiohttpClientMocker, method: str) -> dict[str, Any]:
    """Return the JSON body of the most recent request with a given method."""
    body = last_call(aioclient_mock, method)[2]
    return json.loads(body) if isinstance(body, str | bytes) else body


async def test_add_item_parses_trailing_quantity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Typing 'Eggs x6' sets the name and the quantity separately."""
    await setup_helm(hass, aioclient_mock, config_entry)
    aioclient_mock.post(
        ITEMS_URL, status=201, json={"data": {"id": 93, "name": "Eggs"}}
    )

    await hass.services.async_call(
        "todo", "add_item", {"entity_id": ENTITY, "item": "Eggs x6"}, blocking=True
    )

    assert last_body(aioclient_mock, "post") == {"name": "Eggs", "qty": 6}


async def test_add_item_without_quantity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A plain name is sent as-is, with no qty field."""
    await setup_helm(hass, aioclient_mock, config_entry)
    aioclient_mock.post(
        ITEMS_URL, status=201, json={"data": {"id": 94, "name": "Rice"}}
    )

    await hass.services.async_call(
        "todo", "add_item", {"entity_id": ENTITY, "item": "Rice"}, blocking=True
    )

    assert last_body(aioclient_mock, "post") == {"name": "Rice"}


async def test_add_item_sends_an_idempotency_key(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Creates carry a replay guard so a retry cannot duplicate the item."""
    await setup_helm(hass, aioclient_mock, config_entry)
    aioclient_mock.post(ITEMS_URL, status=201, json={"data": {"id": 95, "name": "Tea"}})

    await hass.services.async_call(
        "todo", "add_item", {"entity_id": ENTITY, "item": "Tea"}, blocking=True
    )

    headers = last_call(aioclient_mock, "post")[3]
    assert headers["Idempotency-Key"].startswith("ha-")


async def test_ticking_sends_only_completed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A tick uses the API's completion-toggle path, not a full edit."""
    await setup_helm(hass, aioclient_mock, config_entry)
    aioclient_mock.patch(
        f"{ITEMS_URL}/91", json={"data": {"id": 91, "completed": True}}
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": ENTITY, "item": "Milk ×2", "status": "completed"},
        blocking=True,
    )

    assert last_body(aioclient_mock, "patch") == {"completed": True}


async def test_rename_sends_name_and_quantity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Renaming an item splits the quantity back out of the summary."""
    await setup_helm(hass, aioclient_mock, config_entry)
    aioclient_mock.patch(
        f"{ITEMS_URL}/91", json={"data": {"id": 91, "name": "Oat milk"}}
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": ENTITY, "item": "Milk ×2", "rename": "Oat milk x3"},
        blocking=True,
    )

    assert last_body(aioclient_mock, "patch") == {"name": "Oat milk", "qty": 3}


async def test_remove_item(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Removing an item issues a DELETE for its ID."""
    await setup_helm(hass, aioclient_mock, config_entry)
    aioclient_mock.delete(f"{ITEMS_URL}/91", status=204, text="")

    await hass.services.async_call(
        "todo", "remove_item", {"entity_id": ENTITY, "item": "Milk ×2"}, blocking=True
    )

    _method, url, _body, _headers = last_call(aioclient_mock, "delete")
    assert str(url).endswith("/shopping-list/items/91")


async def test_read_only_token_cannot_write(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    read_only_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Without shopping:write the entity advertises no write features."""
    await setup_helm(hass, aioclient_mock, read_only_entry, planning=False, write=False)

    state = hass.states.get(ENTITY)
    assert state.attributes["supported_features"] == 0
