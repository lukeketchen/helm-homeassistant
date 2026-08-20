"""Fixtures for the Helm integration tests."""

from __future__ import annotations

from copy import deepcopy
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
ALL_ABILITIES = [
    "planning:read",
    "planning:write",
    "shopping:read",
    "shopping:write",
]

USER = {"id": 4, "name": "Luke", "role": "member"}

LUKE = {"type": "user", "id": 4, "name": "Luke"}
SAM = {"type": "user", "id": 5, "name": "Sam"}
# Same numeric ID as Luke, different type - these must not be confused.
JACK = {"type": "family_member", "id": 4, "name": "Jack"}
# Luke again, as Helm also lists him among the family members. One human.
LUKE_AS_FAMILY = {"type": "family_member", "id": 7, "name": "Luke"}
MEMBERS = [LUKE, SAM, JACK, LUKE_AS_FAMILY]

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


def occurrence(
    kind: str,
    record_id: int,
    title: str,
    stamp: str,
    *,
    starts_at: str | None = None,
    ends_at: str | None = None,
    all_day: bool = False,
    assignees: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build an occurrence in the API's uniform shape.

    Every endpoint returns the same core keys; per-type endpoints add to them
    without reshaping. The composite `id` already identifies one occurrence on
    one day, and `source.id` is the underlying record that write endpoints take.
    """
    return {
        "id": f"{kind}:{record_id}:{stamp}",
        "type": kind,
        "title": title,
        "date": stamp,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "all_day": all_day,
        "assignees": assignees if assignees is not None else [],
        "details": details or {},
        "source": {"type": kind, "id": record_id},
        **extra,
    }


def planning_fixture(today: date) -> dict[str, list[dict[str, Any]]]:
    """Return occurrences covering shared, personal and unattributed items."""
    stamp = today.isoformat()
    # `owner` carries no type key - it is always a user.
    luke_owner = {"id": 4, "name": "Luke"}
    sam_owner = {"id": 5, "name": "Sam"}

    def meal(record_id, title, hour, owner, assignees):
        meal_time = "dinner" if hour.startswith("18") else "lunch"
        return occurrence(
            "meal",
            record_id,
            title,
            stamp,
            starts_at=f"{stamp}T{hour}:00+10:00",
            assignees=assignees,
            details={"meal_time": meal_time, "url": None},
            meal_time=meal_time,
            url=None,
            owner=owner,
            participants=assignees,
        )

    return {
        "meals": [
            # Eaten together - must appear on both Luke's and Sam's calendars.
            meal(1, "Lasagne", "18:30", luke_owner, [LUKE, LUKE_AS_FAMILY, SAM]),
            # Separate lunches - one calendar each.
            meal(2, "Chicken wrap", "12:30", luke_owner, [LUKE, LUKE_AS_FAMILY]),
            meal(3, "Sushi", "12:30", sam_owner, [SAM]),
        ],
        "exercises": [
            occurrence(
                "exercise",
                10,
                "Run",
                stamp,
                starts_at=f"{stamp}T06:00:00+10:00",
                assignees=[LUKE],
                details={
                    "duration_minutes": 45,
                    "subtype": "cardio",
                    "category": "running",
                },
                duration_minutes=45,
                subtype="cardio",
                category="running",
                owner=luke_owner,
                participants=[LUKE],
            ),
            occurrence(
                "exercise",
                11,
                "Yoga",
                stamp,
                starts_at=f"{stamp}T16:15:00+10:00",
                assignees=[SAM],
                details={
                    "duration_minutes": 60,
                    "subtype": None,
                    "category": None,
                },
                duration_minutes=60,
                subtype=None,
                category=None,
                owner=sam_owner,
                participants=[SAM],
            ),
        ],
        "events": [
            # Nobody assigned - belongs on the household calendar.
            occurrence(
                "event",
                20,
                "School concert",
                stamp,
                all_day=True,
                assignees=[],
                details={"color": "#ff0000"},
                color="#ff0000",
            )
        ],
        "chores": [
            occurrence(
                "chore",
                12,
                "Bins out",
                stamp,
                starts_at=f"{stamp}T19:00:00+10:00",
                assignees=[JACK],
                details={"completed": False},
                completed=False,
            )
        ],
        "habits": [
            occurrence(
                "habit",
                7,
                "Read",
                stamp,
                all_day=True,
                assignees=[LUKE],
                details={"completed": True},
                completed=True,
                owner=luke_owner,
            )
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
    """Build a /me response.

    Deep-copied so a test that edits the payload cannot corrupt the shared
    fixture constants for every test that runs after it.
    """
    return deepcopy(
        {
            "data": {
                "user": USER,
                "team": TEAM,
                "abilities": abilities if abilities is not None else ALL_ABILITIES,
                "timezone": HOUSEHOLD_TZ,
                "credential": {**CREDENTIAL, "expires_at": expires_at},
            }
        }
    )


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
            abilities.append("planning:write")
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
            CONF_USER: deepcopy(USER),
            CONF_TEAM: deepcopy(TEAM),
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
