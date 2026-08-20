"""Tests for the uniform occurrence shape the API now returns."""

from __future__ import annotations

from datetime import date

from custom_components.helm.calendar import (
    _to_calendar_event,
    occurrence_field,
    occurrence_people,
    person_key,
    source_record_id,
    source_type,
)

DAY = "2026-08-18"


def _chore(**overrides):
    """Return a chore occurrence in the current API shape."""
    return {
        "id": f"chore:12:{DAY}",
        "type": "chore",
        "title": "Bins out",
        "date": DAY,
        "starts_at": f"{DAY}T19:00:00+10:00",
        "ends_at": None,
        "all_day": False,
        "assignees": [{"type": "user", "id": 4, "name": "Luke"}],
        "details": {"completed": False},
        "source": {"type": "chore", "id": 12},
        **overrides,
    }


def test_uid_is_the_composite_id() -> None:
    """The ID already identifies one occurrence on one day."""
    event = _to_calendar_event(_chore())
    assert event.uid == f"chore:12:{DAY}"


def test_uid_falls_back_for_older_servers() -> None:
    """A bare record ID still needs the date appending to be unique."""
    event = _to_calendar_event(_chore(id=12))
    assert event.uid == f"chore-12-{DAY}"


def test_source_is_read_as_an_object() -> None:
    """Source is {type, id} - the description must not print a dict."""
    description = _to_calendar_event(_chore()).description
    assert "Source: chore" in description
    assert "{" not in description


def test_source_still_accepts_a_plain_string() -> None:
    """Older servers sent source as a string."""
    assert source_type(_chore(source="chores")) == "chores"
    assert source_record_id(_chore(source="chores")) is None


def test_source_record_id_is_the_writable_id() -> None:
    """Write endpoints take the record ID, not the occurrence ID."""
    assert source_record_id(_chore()) == 12


def test_details_are_read_when_fields_are_not_top_level() -> None:
    """/schedule gathers type-specific fields under details."""
    schedule_meal = {
        "id": f"meal:1:{DAY}",
        "type": "meal",
        "title": "Lasagne",
        "date": DAY,
        "starts_at": f"{DAY}T18:30:00+10:00",
        "ends_at": None,
        "all_day": False,
        "assignees": [],
        "details": {"meal_time": "dinner", "url": "https://example.com/r"},
        "source": {"type": "meal", "id": 1},
    }
    description = _to_calendar_event(schedule_meal).description
    assert "Meal: dinner" in description
    assert "https://example.com/r" in description
    assert "{" not in description


def test_top_level_fields_win_over_details() -> None:
    """Per-type endpoints carry the fields directly; both agree."""
    occurrence = _chore(completed=True, details={"completed": False})
    assert occurrence_field(occurrence, "completed") is True


def test_duration_from_details_sets_the_end_time() -> None:
    """A /schedule exercise with no ends_at still gets a sensible length."""
    exercise = {
        "id": f"exercise:10:{DAY}",
        "type": "exercise",
        "title": "Run",
        "date": DAY,
        "starts_at": f"{DAY}T06:00:00+10:00",
        "ends_at": None,
        "all_day": False,
        "assignees": [],
        "details": {"duration_minutes": 45},
        "source": {"type": "exercise", "id": 10},
    }
    event = _to_calendar_event(exercise)
    assert (event.end - event.start).total_seconds() == 45 * 60


def test_assignees_are_authoritative() -> None:
    """Assignees resolve the per-type rules, so owner is not consulted."""
    meal = _chore(
        type="meal",
        assignees=[{"type": "user", "id": 5, "name": "Sam"}],
        owner={"id": 4, "name": "Luke"},
    )
    assert [p["name"] for p in occurrence_people(meal)] == ["Sam"]


def test_empty_assignees_means_nobody() -> None:
    """An unassigned event belongs to the household, not to its owner."""
    assert occurrence_people(_chore(type="event", assignees=[])) == []


def test_owner_is_used_only_without_assignees() -> None:
    """Older servers had no assignees on meals; fall back to owner."""
    legacy = {
        "id": 1,
        "type": "meal",
        "title": "Lasagne",
        "date": DAY,
        "owner": {"type": "user", "id": 4, "name": "Luke"},
        "participants": [{"type": "user", "id": 5, "name": "Sam"}],
    }
    assert sorted(p["name"] for p in occurrence_people(legacy)) == ["Luke", "Sam"]


def test_chore_assignees_now_carry_a_type() -> None:
    """Assignees gained a type key, so they match roster identities."""
    person = occurrence_people(_chore())[0]
    assert person_key(person) == "user:4"


def test_date_only_occurrence_still_works() -> None:
    """All-day entries span one day."""
    event = _to_calendar_event(_chore(all_day=True, starts_at=None))
    assert event.start == date.fromisoformat(DAY)
