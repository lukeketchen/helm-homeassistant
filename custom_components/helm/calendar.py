"""Calendar entities for Helm planning data."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_TEAM, DEFAULT_EVENT_MINUTES, PLANNING_TYPES
from .coordinator import HelmPlanningCoordinator
from .entity import HelmEntity

if TYPE_CHECKING:
    from . import HelmConfigEntry


def person_key(person: dict[str, Any]) -> str:
    """Identify a person by type and ID.

    A `user` with ID 4 and a `family_member` with ID 4 are different people,
    so the type is part of the key.
    """
    return f"{person.get('type')}:{person.get('id')}"


def occurrence_people(occurrence: dict[str, Any]) -> list[dict[str, Any]]:
    """Return everyone attached to an occurrence, de-duplicated.

    Meals and exercises carry `owner` plus `participants`, events and chores
    carry `assignees`, and habits carry `owner` alone.
    """
    people: dict[str, dict[str, Any]] = {}

    owner = occurrence.get("owner")
    if isinstance(owner, dict) and owner.get("id") is not None:
        people[person_key(owner)] = owner

    for field in ("participants", "assignees"):
        for person in occurrence.get(field) or []:
            if isinstance(person, dict) and person.get("id") is not None:
                people.setdefault(person_key(person), person)

    return list(people.values())


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HelmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up per-type, per-person, household and merged calendars."""
    coordinator = entry.runtime_data.planning
    if coordinator is None:
        return

    entities: list[HelmBaseCalendar] = [
        HelmTypeCalendar(coordinator, entry, planning_type)
        for planning_type in PLANNING_TYPES
    ]
    entities.append(HelmScheduleCalendar(coordinator, entry))

    # One calendar per household member, from the roster /me hands back, so
    # entities stay put whether or not someone has anything scheduled.
    members = (entry.data.get(CONF_TEAM) or {}).get("members") or []
    entities.extend(
        HelmPersonCalendar(coordinator, entry, member)
        for member in members
        if isinstance(member, dict) and member.get("id") is not None
    )
    if members:
        entities.append(HelmHouseholdCalendar(coordinator, entry))

    async_add_entities(entities)


class HelmBaseCalendar(HelmEntity, CalendarEntity):
    """Shared behaviour for every Helm calendar.

    Subclasses choose which planning types to request and which occurrences
    within them to keep.
    """

    coordinator: HelmPlanningCoordinator

    @property
    def _types(self) -> list[str]:
        """Planning types this calendar draws from."""
        return list(PLANNING_TYPES)

    def _include(self, occurrence: dict[str, Any]) -> bool:
        """Whether an occurrence belongs on this calendar."""
        return True

    def _cached_occurrences(self) -> list[dict[str, Any]]:
        """Return cached occurrences for this calendar, in display order."""
        source = (
            self.coordinator.occurrences(self._types[0])
            if len(self._types) == 1
            else self.coordinator.all_occurrences()
        )
        return [occurrence for occurrence in source if self._include(occurrence)]

    @property
    def event(self) -> CalendarEvent | None:
        """Return the event in progress, or the next one to start."""
        now = self.coordinator.now()
        upcoming: CalendarEvent | None = None

        for occurrence in self._cached_occurrences():
            candidate = _to_calendar_event(occurrence)
            if candidate is None:
                continue
            start, end = _as_datetimes(candidate, now.tzinfo)
            if start <= now < end:
                return candidate
            if start > now and (
                upcoming is None or start < _as_datetimes(upcoming, now.tzinfo)[0]
            ):
                upcoming = candidate

        return upcoming

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the timezone the range was resolved in."""
        return {"timezone": self.coordinator.timezone_name}

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return events in a range, fetching beyond the cached window."""
        tz = self.coordinator.timezone or dt_util.DEFAULT_TIME_ZONE
        start = start_date.astimezone(tz).date()
        end = end_date.astimezone(tz).date()

        occurrences = await self.coordinator.async_fetch_range(start, end, self._types)
        events = [
            event
            for occurrence in occurrences
            if self._include(occurrence) and (event := _to_calendar_event(occurrence))
        ]
        return [event for event in events if _overlaps(event, start_date, end_date)]


class HelmTypeCalendar(HelmBaseCalendar):
    """Every occurrence of one planning type."""

    def __init__(
        self,
        coordinator: HelmPlanningCoordinator,
        entry: HelmConfigEntry,
        planning_type: str,
    ) -> None:
        """Initialise the calendar."""
        super().__init__(coordinator, entry, f"calendar_{planning_type}")
        self._planning_type = planning_type
        self._attr_translation_key = f"calendar_{planning_type}"

    @property
    def _types(self) -> list[str]:
        """Only this calendar's planning type."""
        return [self._planning_type]


class HelmScheduleCalendar(HelmBaseCalendar):
    """Everything, merged."""

    _attr_translation_key = "calendar_schedule"

    def __init__(
        self, coordinator: HelmPlanningCoordinator, entry: HelmConfigEntry
    ) -> None:
        """Initialise the calendar."""
        super().__init__(coordinator, entry, "calendar_schedule")


class HelmPersonCalendar(HelmBaseCalendar):
    """Everything one household member is involved in.

    An occurrence lands on the calendar of every person attached to it, so a
    dinner you both eat shows on both calendars while your separate lunches
    show on one each.
    """

    def __init__(
        self,
        coordinator: HelmPlanningCoordinator,
        entry: HelmConfigEntry,
        person: dict[str, Any],
    ) -> None:
        """Initialise the calendar."""
        key = person_key(person).replace(":", "_")
        super().__init__(coordinator, entry, f"calendar_person_{key}")
        self._person_key = person_key(person)
        self._attr_name = person.get("name") or "Unknown"

    def _include(self, occurrence: dict[str, Any]) -> bool:
        """Keep occurrences this person is attached to."""
        return any(
            person_key(person) == self._person_key
            for person in occurrence_people(occurrence)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose who this calendar is for."""
        return {**super().extra_state_attributes, "person": self._person_key}


class HelmHouseholdCalendar(HelmBaseCalendar):
    """Occurrences that belong to nobody in particular."""

    _attr_translation_key = "calendar_household"

    def __init__(
        self, coordinator: HelmPlanningCoordinator, entry: HelmConfigEntry
    ) -> None:
        """Initialise the calendar."""
        super().__init__(coordinator, entry, "calendar_household")

    def _include(self, occurrence: dict[str, Any]) -> bool:
        """Keep only unattributed occurrences."""
        return not occurrence_people(occurrence)


def _as_datetimes(event: CalendarEvent, tzinfo: Any) -> tuple[datetime, datetime]:
    """Return an event's bounds as aware datetimes for comparison."""
    start = event.start
    end = event.end
    if isinstance(start, datetime):
        return start, end  # type: ignore[return-value]
    zone = tzinfo or dt_util.DEFAULT_TIME_ZONE
    return (
        datetime.combine(start, datetime.min.time(), tzinfo=zone),
        datetime.combine(end, datetime.min.time(), tzinfo=zone),
    )


def _overlaps(event: CalendarEvent, start: datetime, end: datetime) -> bool:
    """Return True when the event intersects the requested window."""
    event_start, event_end = _as_datetimes(event, start.tzinfo)
    return event_start < end and event_end > start


def _to_calendar_event(occurrence: dict[str, Any]) -> CalendarEvent | None:
    """Convert a Helm occurrence into a Home Assistant calendar event."""
    raw_date = occurrence.get("date")
    title = occurrence.get("title") or "(untitled)"
    if not isinstance(raw_date, str):
        return None
    try:
        day = date.fromisoformat(raw_date)
    except ValueError:
        return None

    uid = f"{occurrence.get('type')}-{occurrence.get('id')}-{raw_date}"
    description = _describe(occurrence)

    starts_at = occurrence.get("starts_at")
    if occurrence.get("all_day") or not starts_at:
        return CalendarEvent(
            start=day,
            end=day + timedelta(days=1),
            summary=title,
            description=description,
            uid=uid,
        )

    start = dt_util.parse_datetime(starts_at)
    if start is None:
        return None

    end = None
    if ends_at := occurrence.get("ends_at"):
        end = dt_util.parse_datetime(ends_at)
    if end is None:
        minutes = occurrence.get("duration_minutes") or DEFAULT_EVENT_MINUTES
        end = start + timedelta(minutes=int(minutes))
    if end <= start:
        end = start + timedelta(minutes=DEFAULT_EVENT_MINUTES)

    return CalendarEvent(
        start=start, end=end, summary=title, description=description, uid=uid
    )


def _names(people: list[dict[str, Any]]) -> str:
    """Join people's names."""
    return ", ".join(person["name"] for person in people if person.get("name"))


def _describe(occurrence: dict[str, Any]) -> str | None:
    """Build a readable description from the type-specific fields."""
    lines: list[str] = []
    kind = occurrence.get("type")
    if kind:
        lines.append(f"Type: {kind}")
    if meal_time := occurrence.get("meal_time"):
        lines.append(f"Meal: {meal_time}")
    if category := occurrence.get("category"):
        lines.append(f"Category: {category}")
    if subtype := occurrence.get("subtype"):
        lines.append(f"Subtype: {subtype}")
    if (duration := occurrence.get("duration_minutes")) is not None:
        lines.append(f"Duration: {duration} min")
    if (completed := occurrence.get("completed")) is not None:
        lines.append("Completed: yes" if completed else "Completed: no")
    if people := _names(occurrence_people(occurrence)):
        lines.append(f"Who: {people}")
    if details := occurrence.get("details"):
        lines.append(str(details))
    if url := occurrence.get("url"):
        lines.append(str(url))
    if source := occurrence.get("source"):
        lines.append(f"Source: {source}")

    return "\n".join(lines) or None
