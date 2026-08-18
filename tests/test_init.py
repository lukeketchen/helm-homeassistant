"""Tests for entity setup across the Helm platforms."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import setup_helm


async def test_all_platforms_load(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A fully abled token produces calendars, a to-do list and sensors."""
    await setup_helm(hass, aioclient_mock, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED

    for entity_id in (
        "calendar.helm_schedule",
        "calendar.helm_meals",
        "calendar.helm_exercise",
        "calendar.helm_events",
        "calendar.helm_chores",
        "calendar.helm_habits",
        "todo.helm_shopping_list",
        "sensor.helm_next_up",
        "sensor.helm_today",
        "sensor.helm_meals_today",
        "sensor.helm_chores_outstanding",
        "sensor.helm_shopping_list_outstanding",
    ):
        assert hass.states.get(entity_id) is not None, f"{entity_id} was not created"


async def test_sensor_values(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Counts reflect the fixture, and completion is honoured."""
    await setup_helm(hass, aioclient_mock, config_entry)

    assert hass.states.get("sensor.helm_today").state == "5"
    assert hass.states.get("sensor.helm_meals_today").state == "1"
    # The chore is not completed, the habit is.
    assert hass.states.get("sensor.helm_chores_outstanding").state == "1"
    assert hass.states.get("sensor.helm_habits_outstanding").state == "0"
    # Milk is outstanding, bread is done.
    assert hass.states.get("sensor.helm_shopping_list_outstanding").state == "1"


async def test_todo_list_contents(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The to-do list shows both items, with quantity in the summary."""
    await setup_helm(hass, aioclient_mock, config_entry)

    state = hass.states.get("todo.helm_shopping_list")
    assert state.state == "1"  # one needs_action item

    items = await hass.services.async_call(
        "todo",
        "get_items",
        {"entity_id": "todo.helm_shopping_list"},
        blocking=True,
        return_response=True,
    )
    summaries = [item["summary"] for item in items["todo.helm_shopping_list"]["items"]]
    assert summaries == ["Milk ×2", "Bread"]


async def test_calendar_event_shapes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Timed, all-day and duration-only occurrences all convert correctly."""
    today = await setup_helm(hass, aioclient_mock, config_entry)

    start = dt_util.start_of_local_day(today) - timedelta(days=1)
    end = start + timedelta(days=3)

    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.helm_schedule",
            "start_date_time": start.isoformat(),
            "end_date_time": end.isoformat(),
        },
        blocking=True,
        return_response=True,
    )
    found = {
        event["summary"]: event for event in events["calendar.helm_schedule"]["events"]
    }

    assert set(found) == {"Lasagne", "Run", "School concert", "Bins out", "Read"}
    # All-day entries come back as bare dates spanning one day.
    assert found["School concert"]["start"] == today.isoformat()
    assert found["School concert"]["end"] == (today + timedelta(days=1)).isoformat()
    # The exercise has no ends_at, so duration_minutes fills it in.
    assert found["Run"]["start"].endswith("06:00:00+10:00")
    assert found["Run"]["end"].endswith("06:45:00+10:00")


async def test_read_only_token_creates_no_calendars(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    read_only_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Without planning:read, the calendar platform is never forwarded."""
    await setup_helm(hass, aioclient_mock, read_only_entry, planning=False, write=False)

    assert read_only_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("calendar.helm_schedule") is None
    assert hass.states.get("sensor.helm_next_up") is None
    assert hass.states.get("todo.helm_shopping_list") is not None


async def test_unload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The entry unloads cleanly."""
    await setup_helm(hass, aioclient_mock, config_entry)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
