"""Tests for ticking chores and habits off."""

from __future__ import annotations

import json

from homeassistant.components.todo import TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import BASE_URL, _entry, me_payload, setup_helm

CHORES = "todo.helm_chores_today"
HABITS = "todo.helm_habits_today"


async def _items(hass: HomeAssistant, entity_id: str) -> list[dict]:
    """Return the to-do items on a list."""
    result = await hass.services.async_call(
        "todo",
        "get_items",
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    return result[entity_id]["items"]


async def test_lists_are_created_with_planning_write(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Chores and habits get their own to-do lists."""
    await setup_helm(hass, aioclient_mock, config_entry)

    assert hass.states.get(CHORES) is not None
    assert hass.states.get(HABITS) is not None


async def test_lists_are_tick_only(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Helm owns creating and deleting these, so only update is offered.

    That makes the stock to-do card render checkboxes with no "Add item"
    field and no "Clear completed" button.
    """
    features = hass.states.get(CHORES)
    await setup_helm(hass, aioclient_mock, config_entry)
    features = hass.states.get(CHORES).attributes["supported_features"]

    assert features == TodoListEntityFeature.UPDATE_TODO_ITEM
    assert not features & TodoListEntityFeature.CREATE_TODO_ITEM
    assert not features & TodoListEntityFeature.DELETE_TODO_ITEM


async def test_only_todays_occurrences_are_listed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A flat list would otherwise repeat a daily chore once per day."""
    today = await setup_helm(hass, aioclient_mock, config_entry)

    items = await _items(hass, CHORES)
    assert [item["summary"] for item in items] == ["Bins out"]
    # The UID is the composite occurrence ID, unique to this day.
    assert items[0]["uid"] == f"chore:12:{today.isoformat()}"
    assert items[0]["status"] == "needs_action"

    habits = await _items(hass, HABITS)
    assert [item["summary"] for item in habits] == ["Read"]
    assert habits[0]["status"] == "completed"


async def test_ticking_patches_the_record_not_the_occurrence(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The write endpoint takes source.id and the date."""
    today = await setup_helm(hass, aioclient_mock, config_entry)
    stamp = today.isoformat()

    aioclient_mock.patch(
        f"{BASE_URL}/chores/12/occurrences/{stamp}",
        json={
            "data": {
                "id": f"chore:12:{stamp}",
                "type": "chore",
                "title": "Bins out",
                "date": stamp,
                "starts_at": None,
                "ends_at": None,
                "all_day": True,
                "assignees": [],
                "details": {"completed": True},
                "source": {"type": "chore", "id": 12},
                "completed": True,
            }
        },
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": CHORES, "item": "Bins out", "status": "completed"},
        blocking=True,
    )

    patch = next(
        call
        for call in reversed(aioclient_mock.mock_calls)
        if call[0].lower() == "patch"
    )
    assert patch[1].path == f"/api/v1/chores/12/occurrences/{stamp}"
    body = patch[2]
    assert (
        json.loads(body)
        if isinstance(body, str | bytes)
        else body == {"completed": True}
    )


async def test_the_response_updates_state_without_refetching(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The write returns a full occurrence, so no six-endpoint refresh runs."""
    today = await setup_helm(hass, aioclient_mock, config_entry)
    stamp = today.isoformat()
    aioclient_mock.patch(
        f"{BASE_URL}/chores/12/occurrences/{stamp}",
        json={
            "data": {
                "id": f"chore:12:{stamp}",
                "type": "chore",
                "title": "Bins out",
                "date": stamp,
                "starts_at": None,
                "ends_at": None,
                "all_day": True,
                "assignees": [],
                "details": {"completed": True},
                "source": {"type": "chore", "id": 12},
                "completed": True,
            }
        },
    )
    aioclient_mock.mock_calls.clear()

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": CHORES, "item": "Bins out", "status": "completed"},
        blocking=True,
    )
    await hass.async_block_till_done()

    # One write, and no planning GETs behind it.
    methods = [call[0].lower() for call in aioclient_mock.mock_calls]
    assert methods == ["patch"], methods

    items = await _items(hass, CHORES)
    assert items[0]["status"] == "completed"
    # The outstanding sensor sees it too.
    assert hass.states.get("sensor.helm_chores_outstanding").state == "0"


async def test_a_failed_tick_surfaces_the_api_message(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A chore that does not recur today is rejected with a clear error."""
    today = await setup_helm(hass, aioclient_mock, config_entry)
    aioclient_mock.patch(
        f"{BASE_URL}/chores/12/occurrences/{today.isoformat()}",
        status=422,
        json={
            "error": {
                "code": "validation_failed",
                "message": "The chore does not occur on that date.",
            }
        },
    )

    with pytest.raises(HomeAssistantError, match="does not occur"):
        await hass.services.async_call(
            "todo",
            "update_item",
            {"entity_id": CHORES, "item": "Bins out", "status": "completed"},
            blocking=True,
        )


async def test_no_lists_without_planning_write(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    enable_custom_integrations,
) -> None:
    """A read-only planning token gets calendars but no tickable lists."""
    abilities = ["planning:read", "shopping:read"]
    entry = _entry(abilities)
    await setup_helm(hass, aioclient_mock, entry, me=me_payload(abilities))

    assert hass.states.get("calendar.helm_schedule") is not None
    assert hass.states.get(CHORES) is None
    assert hass.states.get(HABITS) is None


def _response(
    stamp: str, *, completed: bool, uid: str | None = None, nested_only=False
):
    """Build a PATCH response describing a chore occurrence."""
    occurrence = {
        "id": uid or f"chore:12:{stamp}",
        "type": "chore",
        "title": "Bins out",
        "date": stamp,
        "starts_at": None,
        "ends_at": None,
        "all_day": True,
        "assignees": [],
        "details": {"completed": completed},
        "source": {"type": "chore", "id": 12},
    }
    if not nested_only:
        occurrence["completed"] = completed
    return {"data": occurrence}


async def test_completed_is_read_from_details_too(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A response nesting completed under details must still register."""
    today = await setup_helm(hass, aioclient_mock, config_entry)
    stamp = today.isoformat()
    aioclient_mock.patch(
        f"{BASE_URL}/chores/12/occurrences/{stamp}",
        json=_response(stamp, completed=True, nested_only=True),
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": CHORES, "item": "Bins out", "status": "completed"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert (await _items(hass, CHORES))[0]["status"] == "completed"


async def test_the_date_comes_from_the_occurrence(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The occurrence knows its own day; today is not recomputed."""
    today = await setup_helm(hass, aioclient_mock, config_entry)
    stamp = today.isoformat()
    aioclient_mock.patch(
        f"{BASE_URL}/chores/12/occurrences/{stamp}",
        json=_response(stamp, completed=True),
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": CHORES, "item": "Bins out", "status": "completed"},
        blocking=True,
    )

    patch = next(
        c for c in reversed(aioclient_mock.mock_calls) if c[0].lower() == "patch"
    )
    # The date in the path is the occurrence's own date.
    assert patch[1].path.endswith(f"/occurrences/{stamp}")


async def test_a_response_for_another_occurrence_triggers_a_refetch(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Local state must never claim a tick the server did not describe.

    If the response is for a different occurrence, showing it as done would be
    a lie - Home Assistant ticked, Helm did not.
    """
    today = await setup_helm(hass, aioclient_mock, config_entry)
    stamp = today.isoformat()
    aioclient_mock.patch(
        f"{BASE_URL}/chores/12/occurrences/{stamp}",
        json=_response(stamp, completed=True, uid="chore:99:2020-01-01"),
    )
    aioclient_mock.mock_calls.clear()

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": CHORES, "item": "Bins out", "status": "completed"},
        blocking=True,
    )
    await hass.async_block_till_done()

    methods = [c[0].lower() for c in aioclient_mock.mock_calls]
    assert "get" in methods, "a mismatched response should force a refetch"


async def test_a_response_denying_the_change_triggers_a_refetch(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A 200 that still says not-completed must not show as completed."""
    today = await setup_helm(hass, aioclient_mock, config_entry)
    stamp = today.isoformat()
    aioclient_mock.patch(
        f"{BASE_URL}/chores/12/occurrences/{stamp}",
        json=_response(stamp, completed=False),
    )
    aioclient_mock.mock_calls.clear()

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": CHORES, "item": "Bins out", "status": "completed"},
        blocking=True,
    )
    await hass.async_block_till_done()

    methods = [c[0].lower() for c in aioclient_mock.mock_calls]
    assert "get" in methods, "an unconfirmed tick should force a refetch"
    assert (await _items(hass, CHORES))[0]["status"] == "needs_action"
