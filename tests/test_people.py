"""Tests for per-person and household calendars."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.helm.calendar import occurrence_people, person_key
from custom_components.helm.const import (
    CONF_SHOW_PEOPLE,
    CONF_TEAM,
    SHOW_PEOPLE_PREFIX,
    SHOW_PEOPLE_SUFFIX,
)

from .conftest import JACK, LUKE, SAM, me_payload, mock_api, setup_helm


async def _summaries(hass: HomeAssistant, entity_id: str, today) -> set[str]:
    """Return the event summaries a calendar reports for today."""
    start = dt_util.start_of_local_day(today)
    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": entity_id,
            "start_date_time": start.isoformat(),
            "end_date_time": (start + timedelta(days=1)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )
    return {event["summary"] for event in events[entity_id]["events"]}


def test_person_key_separates_types() -> None:
    """A user and a family member with the same ID are different people."""
    assert person_key(LUKE) == "user:4"
    assert person_key(JACK) == "family_member:4"
    assert person_key(LUKE) != person_key(JACK)


def test_occurrence_people_merges_and_dedupes() -> None:
    """Owner, participants and assignees combine without duplicates."""
    occurrence = {"owner": LUKE, "participants": [LUKE, SAM]}
    assert [person_key(p) for p in occurrence_people(occurrence)] == [
        "user:4",
        "user:5",
    ]
    assert occurrence_people({"assignees": [JACK]})[0]["name"] == "Jack"
    assert occurrence_people({"title": "nobody here"}) == []


async def test_a_calendar_per_member(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The roster from /me drives one calendar per household member."""
    await setup_helm(hass, aioclient_mock, config_entry)

    for entity_id in (
        "calendar.helm_luke",
        "calendar.helm_sam",
        "calendar.helm_jack",
        "calendar.helm_household",
        "calendar.helm_schedule",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_shared_dinner_separate_lunches(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The case that motivated this: same dinner, different lunches."""
    today = await setup_helm(hass, aioclient_mock, config_entry)

    luke = await _summaries(hass, "calendar.helm_luke", today)
    sam = await _summaries(hass, "calendar.helm_sam", today)

    # One dinner, on both calendars.
    assert "Lasagne" in luke
    assert "Lasagne" in sam

    # Lunches land on exactly one calendar each.
    assert "Chicken wrap" in luke
    assert "Chicken wrap" not in sam
    assert "Sushi" in sam
    assert "Sushi" not in luke

    # Exercise follows its owner.
    assert luke >= {"Run"}
    assert "Run" not in sam
    assert "Yoga" in sam


async def test_habit_owner_reaches_the_right_person(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Habits now carry an owner, so they no longer fall through to Household."""
    today = await setup_helm(hass, aioclient_mock, config_entry)

    assert "Read" in await _summaries(hass, "calendar.helm_luke", today)
    assert "Read" not in await _summaries(hass, "calendar.helm_household", today)


async def test_family_member_is_not_confused_with_a_user(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Jack is family_member:4; Luke is user:4. The chore is Jack's alone."""
    today = await setup_helm(hass, aioclient_mock, config_entry)

    assert "Bins out" in await _summaries(hass, "calendar.helm_jack", today)
    assert "Bins out" not in await _summaries(hass, "calendar.helm_luke", today)


async def test_household_holds_only_unattributed_items(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Items with nobody attached go to Household, and nothing else does."""
    today = await setup_helm(hass, aioclient_mock, config_entry)

    assert await _summaries(hass, "calendar.helm_household", today) == {
        "School concert"
    }


async def test_person_calendar_state_and_attributes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A person calendar names who it is for."""
    await setup_helm(hass, aioclient_mock, config_entry)

    state = hass.states.get("calendar.helm_sam")
    assert state.attributes["friendly_name"] == "Helm Sam"
    assert state.attributes["person"] == "user:5"


async def test_roster_change_is_picked_up_on_reload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Adding a household member in Helm creates a calendar after a reload."""
    await setup_helm(hass, aioclient_mock, config_entry)
    assert hass.states.get("calendar.helm_robin") is None

    robin = {"type": "family_member", "id": 9, "name": "Robin"}
    payload = me_payload()
    payload["data"]["team"]["members"] = [LUKE, SAM, JACK, robin]

    aioclient_mock.clear_requests()
    mock_api(aioclient_mock, today=dt_util.now().date(), me=payload)

    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("calendar.helm_robin") is not None
    assert config_entry.data[CONF_TEAM]["members"][-1]["name"] == "Robin"


async def test_no_roster_means_no_person_calendars(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A server whose /me has no members still sets up, without person calendars."""
    payload = me_payload()
    payload["data"]["team"] = {"id": 1, "name": "Ketchen"}
    await hass.config.async_set_time_zone("Australia/Sydney")
    mock_api(aioclient_mock, today=dt_util.now().date(), me=payload)

    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("calendar.helm_schedule") is not None
    assert hass.states.get("calendar.helm_luke") is None
    assert hass.states.get("calendar.helm_household") is None


async def _setup_with_people(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    mode: str,
):
    """Set the show_people option before the entry loads."""
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_SHOW_PEOPLE: mode})
    return await setup_helm(hass, aioclient_mock, entry)


async def test_names_are_off_by_default(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Titles are untouched unless the option is turned on."""
    today = await setup_helm(hass, aioclient_mock, config_entry)

    assert "Chicken wrap" in await _summaries(hass, "calendar.helm_schedule", today)


async def test_names_as_a_suffix(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The merged calendar can say who each item is for."""
    today = await _setup_with_people(
        hass, aioclient_mock, config_entry, SHOW_PEOPLE_SUFFIX
    )

    summaries = await _summaries(hass, "calendar.helm_schedule", today)
    assert "Chicken wrap — Luke" in summaries
    assert "Sushi — Sam" in summaries
    # Everyone attached is listed, in owner-then-participants order.
    assert "Lasagne — Luke, Sam" in summaries
    # Nobody attached means nothing to add.
    assert "School concert" in summaries


async def test_names_as_a_prefix(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Prefixing makes a merged agenda easy to scan by person."""
    today = await _setup_with_people(
        hass, aioclient_mock, config_entry, SHOW_PEOPLE_PREFIX
    )

    assert "Luke — Chicken wrap" in await _summaries(
        hass, "calendar.helm_schedule", today
    )


async def test_per_type_calendars_are_labelled_too(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A Meals calendar mixes people, so it benefits from names as well."""
    today = await _setup_with_people(
        hass, aioclient_mock, config_entry, SHOW_PEOPLE_SUFFIX
    )

    assert "Sushi — Sam" in await _summaries(hass, "calendar.helm_meals", today)


async def test_person_and_household_calendars_stay_clean(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Naming Luke on Luke's own calendar would just be noise."""
    today = await _setup_with_people(
        hass, aioclient_mock, config_entry, SHOW_PEOPLE_SUFFIX
    )

    assert "Chicken wrap" in await _summaries(hass, "calendar.helm_luke", today)
    assert await _summaries(hass, "calendar.helm_household", today) == {
        "School concert"
    }
