"""Tests for the Helm integration services."""

from __future__ import annotations

from datetime import timedelta
import json

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.helm.const import DOMAIN

from .conftest import BASE_URL, setup_helm

ITEMS_URL = f"{BASE_URL}/shopping-list/items"


async def test_add_shopping_item_sends_every_field(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The service reaches fields the to-do card cannot set."""
    await setup_helm(hass, aioclient_mock, config_entry)
    aioclient_mock.post(ITEMS_URL, status=201, json={"data": {"id": 96}})

    response = await hass.services.async_call(
        DOMAIN,
        "add_shopping_item",
        {
            "config_entry_id": config_entry.entry_id,
            "name": "Oat milk",
            "qty": 2,
            "url": "https://example.com/oat",
            "shopping_list_type_id": 3,
            "visible_to_extended": True,
            "idempotency_key": "weekly-oat-2026-W34",
        },
        blocking=True,
        return_response=True,
    )

    post = next(
        call
        for call in reversed(aioclient_mock.mock_calls)
        if call[0].lower() == "post"
    )
    body = post[2]
    if isinstance(body, str | bytes):
        body = json.loads(body)
    assert body == {
        "name": "Oat milk",
        "qty": 2,
        "url": "https://example.com/oat",
        "shopping_list_type_id": 3,
        "visible_to_extended": True,
    }
    assert post[3]["Idempotency-Key"] == "weekly-oat-2026-W34"
    assert response["item"] == {"id": 96}


async def test_update_requires_at_least_one_field(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """An update with nothing to change is rejected before any request."""
    await setup_helm(hass, aioclient_mock, config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "update_shopping_item",
            {"config_entry_id": config_entry.entry_id, "item_id": 91},
            blocking=True,
        )


async def test_service_rejects_a_missing_ability(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    read_only_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A read-only token cannot be talked into writing."""
    await setup_helm(hass, aioclient_mock, read_only_entry, planning=False, write=False)

    with pytest.raises(ServiceValidationError, match="shopping:write"):
        await hass.services.async_call(
            DOMAIN,
            "add_shopping_item",
            {"config_entry_id": read_only_entry.entry_id, "name": "Milk"},
            blocking=True,
        )


async def test_get_planning_returns_occurrences(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The response service returns sorted occurrences plus range metadata."""
    today = await setup_helm(hass, aioclient_mock, config_entry)

    response = await hass.services.async_call(
        DOMAIN,
        "get_planning",
        {"config_entry_id": config_entry.entry_id, "from": today.isoformat()},
        blocking=True,
        return_response=True,
    )

    assert response["count"] == 8
    assert response["meta"]["timezone"] == "Australia/Sydney"
    titles = [item["title"] for item in response["occurrences"]]
    # All-day entries first (alphabetically), then by start time.
    assert titles[:2] == ["Read", "School concert"]
    assert titles[2:] == [
        "Run",
        "Chicken wrap",
        "Sushi",
        "Yoga",
        "Lasagne",
        "Bins out",
    ]


async def test_get_planning_filters_by_type(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Asking for one type returns only that type."""
    today = await setup_helm(hass, aioclient_mock, config_entry)

    response = await hass.services.async_call(
        DOMAIN,
        "get_planning",
        {
            "config_entry_id": config_entry.entry_id,
            "from": today.isoformat(),
            "types": ["chore"],
        },
        blocking=True,
        return_response=True,
    )

    assert [item["title"] for item in response["occurrences"]] == ["Bins out"]


async def test_get_planning_rejects_a_backwards_range(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """'to' before 'from' is caught locally."""
    today = await setup_helm(hass, aioclient_mock, config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "get_planning",
            {
                "config_entry_id": config_entry.entry_id,
                "from": today.isoformat(),
                "to": (today - timedelta(days=1)).isoformat(),
            },
            blocking=True,
            return_response=True,
        )


async def test_get_planning_chunks_a_long_range(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A range past the cached window is fetched, and split under 31 days."""
    today = await setup_helm(hass, aioclient_mock, config_entry)
    aioclient_mock.mock_calls.clear()

    await hass.services.async_call(
        DOMAIN,
        "get_planning",
        {
            "config_entry_id": config_entry.entry_id,
            "from": (today + timedelta(days=60)).isoformat(),
            "to": (today + timedelta(days=120)).isoformat(),
            "types": ["chore"],
        },
        blocking=True,
        return_response=True,
    )

    chore_calls = [
        call for call in aioclient_mock.mock_calls if call[1].path.endswith("/chores")
    ]
    assert len(chore_calls) == 2
    for _method, url, _body, _headers in chore_calls:
        start = url.query["from"]
        end = url.query["to"]
        assert start <= end
