"""Fixtures for the Helm integration tests."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from homeassistant.const import CONF_API_TOKEN
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.helm.const import (
    CONF_ABILITIES,
    CONF_BASE_URL,
    CONF_CREDENTIAL,
    CONF_TEAM,
    CONF_TIMEZONE,
    CONF_USER,
    DOMAIN,
)

BASE_URL = "https://helm.test/api/v1"
TOKEN = "helm_" + "a" * 68
ALL_ABILITIES = ["planning:read", "shopping:read", "shopping:write"]

USER = {"id": 4, "name": "Luke", "role": "member"}

LUKE = {"type": "user", "id": 4, "name": "Luke"}
SAM = {"type": "user", "id": 5, "name": "Sam"}
# Same numeric ID as Luke, different type - these must not be confused.
JACK = {"type": "family_member", "id": 4, "name": "Jack"}
MEMBERS = [LUKE, SAM, JACK]

TEAM = {"id": 1, "name": "Ketchen", "members": MEMBERS}
CREDENTIAL = {"name": "Home Assistant", "expires_at": None}

WRITE_PROBE_URL = f"{BASE_URL}/shopping-list/items/2147483647"
# The fixtures declare this as the household timezone, so tests run in it too.
HOUSEHOLD_TZ = "Australia/Sydney"


def _meta(today: date) -> dict[str, Any]:
    """Return a planning envelope meta block."""
    return {
        "timezone": "Australia/Sydney",
        "from": today.isoformat(),
        "to": today.isoformat(),
    }


def planning_fixture(today: date) -> dict[str, list[dict[str, Any]]]:
    """Return occurrences covering shared, personal and unattributed items."""
    stamp = today.isoformat()

    def meal(id_, title, hour, owner, participants):
        return {
            "id": id_,
            "type": "meal",
            "title": title,
            "date": stamp,
            "starts_at": f"{stamp}T{hour}:00+10:00",
            "all_day": False,
            "meal_time": "dinner" if hour.startswith("18") else "lunch",
            "url": None,
            "owner": owner,
            "participants": participants,
            "source": "meal_plan",
        }

    return {
        "meals": [
            # Eaten together - must appear on both Luke's and Sam's calendars.
            meal(1, "Lasagne", "18:30", LUKE, [LUKE, SAM]),
            # Separate lunches - one calendar each.
            meal(2, "Chicken wrap", "12:30", LUKE, [LUKE]),
            meal(3, "Sushi", "12:30", SAM, [SAM]),
        ],
        "exercises": [
            {
                "id": 10,
                "type": "exercise",
                "title": "Run",
                "date": stamp,
                "starts_at": f"{stamp}T06:00:00+10:00",
                "all_day": False,
                "duration_minutes": 45,
                "subtype": "cardio",
                "category": "running",
                "owner": LUKE,
                "participants": [],
                "source": "routine",
            },
            {
                "id": 11,
                "type": "exercise",
                "title": "Yoga",
                "date": stamp,
                "starts_at": f"{stamp}T16:15:00+10:00",
                "all_day": False,
                "duration_minutes": 60,
                "subtype": None,
                "category": None,
                "owner": SAM,
                "participants": [SAM],
                "source": "routine",
            },
        ],
        "events": [
            # Nobody assigned - belongs on the household calendar.
            {
                "id": 20,
                "type": "event",
                "title": "School concert",
                "date": stamp,
                "starts_at": None,
                "ends_at": None,
                "all_day": True,
                "color": "#ff0000",
                "assignees": [],
                "source": "calendar",
            }
        ],
        "chores": [
            {
                "id": 12,
                "type": "chore",
                "title": "Bins out",
                "date": stamp,
                "starts_at": f"{stamp}T19:00:00+10:00",
                "all_day": False,
                "completed": False,
                "assignees": [JACK],
                "source": "chores",
            }
        ],
        "habits": [
            # Habits now carry an owner, always the token owner.
            {
                "id": 7,
                "type": "habit",
                "title": "Read",
                "date": stamp,
                "starts_at": None,
                "all_day": True,
                "completed": True,
                "owner": LUKE,
                "source": "habits",
            }
        ],
    }


SHOPPING_FIXTURE = [
    {
        "id": 91,
        "name": "Milk",
        "qty": 2,
        "url": None,
        "completed": False,
        "completed_at": None,
        "sort_order": 1,
        "visible_to_extended": False,
        "type": {"id": 3, "name": "Dairy"},
        "meal": {"id": 17, "name": "Lasagne"},
    },
    {
        "id": 92,
        "name": "Bread",
        "qty": 1,
        "url": None,
        "completed": True,
        "completed_at": "2026-08-18T09:00:00+10:00",
        "sort_order": 2,
        "visible_to_extended": False,
        "type": None,
        "meal": None,
    },
]


def me_payload(
    abilities: list[str] | None = None, expires_at: str | None = None
) -> dict[str, Any]:
    """Build a /me response."""
    return {
        "data": {
            "user": USER,
            "team": TEAM,
            "abilities": abilities if abilities is not None else ALL_ABILITIES,
            "timezone": HOUSEHOLD_TZ,
            "credential": {**CREDENTIAL, "expires_at": expires_at},
        }
    }


def mock_api(
    aioclient_mock: AiohttpClientMocker,
    *,
    today: date,
    planning: bool = True,
    shopping: bool = True,
    write: bool = True,
    me: dict[str, Any] | None = None,
    me_status: int = 200,
    expires_at: str | None = None,
) -> None:
    """Register the whole API surface, honouring the abilities under test."""
    if me_status == 200:
        abilities = []
        if planning:
            abilities.append("planning:read")
        if shopping:
            abilities.append("shopping:read")
        if shopping and write:
            abilities.append("shopping:write")
        aioclient_mock.get(
            f"{BASE_URL}/me", json=me or me_payload(abilities, expires_at)
        )
    else:
        aioclient_mock.get(
            f"{BASE_URL}/me",
            status=me_status,
            json={"error": {"code": "not_found", "message": "No such route."}},
        )

    forbidden = {
        "error": {
            "code": "insufficient_ability",
            "message": "The token lacks the required ability.",
        }
    }
    data = planning_fixture(today)

    for endpoint in ("schedule", *data):
        if planning:
            aioclient_mock.get(
                f"{BASE_URL}/{endpoint}",
                json={"data": data.get(endpoint, []), "meta": _meta(today)},
            )
        else:
            aioclient_mock.get(f"{BASE_URL}/{endpoint}", status=403, json=forbidden)

    if shopping:
        aioclient_mock.get(
            f"{BASE_URL}/shopping-list/items", json={"data": SHOPPING_FIXTURE}
        )
    else:
        aioclient_mock.get(
            f"{BASE_URL}/shopping-list/items", status=403, json=forbidden
        )

    if write:
        aioclient_mock.patch(
            WRITE_PROBE_URL,
            status=404,
            json={"error": {"code": "not_found", "message": "No such record."}},
        )
    else:
        aioclient_mock.patch(WRITE_PROBE_URL, status=403, json=forbidden)


def _entry(abilities: list[str], expires_at: str | None = None) -> MockConfigEntry:
    """Build a config entry carrying a given ability set."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Ketchen (Home Assistant)",
        unique_id="helm.test-1-4",
        data={
            CONF_BASE_URL: BASE_URL,
            CONF_API_TOKEN: TOKEN,
            CONF_ABILITIES: abilities,
            CONF_USER: USER,
            CONF_TEAM: TEAM,
            CONF_TIMEZONE: HOUSEHOLD_TZ,
            CONF_CREDENTIAL: {**CREDENTIAL, "expires_at": expires_at},
        },
    )


@pytest.fixture
def read_only_entry() -> MockConfigEntry:
    """Return an entry for a token that can only read the shopping list."""
    return _entry(["shopping:read"])


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a fully abled config entry."""
    return _entry(ALL_ABILITIES)


async def setup_helm(
    hass,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    **abilities: bool,
):
    """Put Home Assistant in the household timezone, mock the API, set up the entry.

    Dates resolve in the token owner's timezone, so the test instance runs in
    the same zone the fixtures declare.
    """
    await hass.config.async_set_time_zone(HOUSEHOLD_TZ)
    today = dt_util.now().date()
    stored = entry.data.get(CONF_CREDENTIAL) or {}
    mock_api(
        aioclient_mock,
        today=today,
        expires_at=stored.get("expires_at"),
        **abilities,
    )

    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return today


@pytest.fixture
def expiring_entry() -> MockConfigEntry:
    """Return an entry whose credential lapses in three days."""
    soon = dt_util.utcnow() + timedelta(days=3)
    return _entry(ALL_ABILITIES, expires_at=soon.isoformat())
