"""Calendar entities for Helm planning data."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SHOW_PEOPLE,
    CONF_TEAM,
    DEFAULT_EVENT_MINUTES,
    DEFAULT_SHOW_PEOPLE,
    PEOPLE_SEPARATOR,
    PLANNING_TYPES,
    SHOW_PEOPLE_PREFIX,
    SHOW_PEOPLE_SUFFIX,
)
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
    # `assignees` is present on every occurrence and already resolves the
    # per-type rules: participants for meals and exercises, falling back to the
    # owner, and the owner for habits. An empty list genuinely means nobody.
    if "assignees" in occurrence:
        return [
            person
            for person in occurrence.get("assignees") or []
            if isinstance(person, dict) and person.get("id") is not None
        ]

    # Older servers, before assignees was added to every endpoint. `owner`
    # carries no type key, so it can only contribute a name.
    people: dict[str, dict[str, Any]] = {}
    owner = occurrence.get("owner")
    if isinstance(owner, dict) and owner.get("id") is not None:
        people[person_key(owner)] = owner
    for person in occurrence.get("participants") or []:
        if isinstance(person, dict) and person.get("id") is not None:
            people.setdefault(person_key(person), person)
    return list(people.values())


def group_by_human(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse roster entries that are the same person into one.

    Helm can list somebody twice — once as the `user` who owns things and once
    as a `family_member` who participates in them. That is one human and should
    be one calendar holding everything, not two holding half each. Entries are
    matched on name; a household with two people sharing a full name would need
    them distinguished in Helm.
    """
    humans: dict[str, dict[str, Any]] = {}

    for member in members:
        if not isinstance(member, dict) or member.get("id") is None:
            continue
        name = str(member.get("name") or "").strip()
        if not name:
            continue
        human = humans.setdefault(
            name.casefold(), {"name": name, "keys": set(), "identities": []}
        )
        human["keys"].add(person_key(member))
        human["identities"].append(member)

    return list(humans.values())


def _stable_identity(human: dict[str, Any]) -> str:
    """Pick the identity a person's entity ID is built from.

    A `user` account is the more durable identity, so prefer it; otherwise take
    the lowest key so the choice does not depend on roster ordering.
    """
    users = sorted(
        person_key(identity)
        for identity in human["identities"]
        if identity.get("type") == "user"
    )
    return users[0] if users else sorted(human["keys"])[0]


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
    humans = group_by_human(members)
    entities.extend(HelmPersonCalendar(coordinator, entry, human) for human in humans)
    if humans:
        entities.append(HelmHouseholdCalendar(coordinator, entry))
        entities.append(HelmSharedCalendar(coordinator, entry))

    async_add_entities(entities)


class HelmBaseCalendar(HelmEntity, CalendarEntity):
    """Shared behaviour for every Helm calendar.

    Subclasses choose which planning types to request and which occurrences
    within them to keep.
    """

    coordinator: HelmPlanningCoordinator

    # Calendars that mix people can label events with who they are for.
    # A person's own calendar and the household one never need it.
    _labels_people = True

    @property
    def _types(self) -> list[str]:
        """Planning types this calendar draws from."""
        return list(PLANNING_TYPES)

    @property
    def _show_people(self) -> str:
        """How, if at all, to name people in event summaries."""
        if not self._labels_people:
            return "off"
        return str(self._entry.options.get(CONF_SHOW_PEOPLE, DEFAULT_SHOW_PEOPLE))

    def _event_from(self, occurrence: dict[str, Any]) -> CalendarEvent | None:
        """Build a calendar event, honouring the people-labelling option."""
        return _to_calendar_event(occurrence, self._show_people)

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
            candidate = self._event_from(occurrence)
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
            if self._include(occurrence) and (event := self._event_from(occurrence))
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

    _labels_people = False

    def __init__(
        self,
        coordinator: HelmPlanningCoordinator,
        entry: HelmConfigEntry,
        human: dict[str, Any],
    ) -> None:
        """Initialise the calendar."""
        slug = _stable_identity(human).replace(":", "_")
        super().__init__(coordinator, entry, f"calendar_person_{slug}")
        # Every identity this person holds, so nothing of theirs is missed.
        self._person_keys: set[str] = set(human["keys"])
        self._attr_name = human["name"]

    def _include(self, occurrence: dict[str, Any]) -> bool:
        """Keep occurrences any of this person's identities are attached to."""
        return any(
            person_key(person) in self._person_keys
            for person in occurrence_people(occurrence)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose which identities this calendar covers."""
        return {
            **super().extra_state_attributes,
            "person": sorted(self._person_keys),
        }


class HelmSharedCalendar(HelmBaseCalendar):
    """Occurrences involving more than one person.

    A dinner you all eat, a joint outing, a chore two people share — but not
    anyone's solo lunch or personal workout.
    """

    _attr_translation_key = "calendar_shared"

    def __init__(
        self, coordinator: HelmPlanningCoordinator, entry: HelmConfigEntry
    ) -> None:
        """Initialise the calendar."""
        super().__init__(coordinator, entry, "calendar_shared")

    def _include(self, occurrence: dict[str, Any]) -> bool:
        """Keep occurrences with two or more distinct people."""
        return len(distinct_names(occurrence_people(occurrence))) >= 2


class HelmHouseholdCalendar(HelmBaseCalendar):
    """Occurrences that belong to nobody in particular."""

    _attr_translation_key = "calendar_household"
    _labels_people = False

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


def _summary(occurrence: dict[str, Any], show_people: str) -> str:
    """Return the event title, optionally naming who it is for."""
    title = occurrence.get("title") or "(untitled)"
    if show_people not in (SHOW_PEOPLE_SUFFIX, SHOW_PEOPLE_PREFIX):
        return title
    people = _names(occurrence_people(occurrence))
    if not people:
        return title
    if show_people == SHOW_PEOPLE_PREFIX:
        return f"{people}{PEOPLE_SEPARATOR}{title}"
    return f"{title}{PEOPLE_SEPARATOR}{people}"


def _to_calendar_event(
    occurrence: dict[str, Any], show_people: str = "off"
) -> CalendarEvent | None:
    """Convert a Helm occurrence into a Home Assistant calendar event."""
    raw_date = occurrence.get("date")
    title = _summary(occurrence, show_people)
    if not isinstance(raw_date, str):
        return None
    try:
        day = date.fromisoformat(raw_date)
    except ValueError:
        return None

    # `id` is a composite - "chore:12:2026-08-18" - so it already identifies a
    # single occurrence on a single day. Older servers sent a bare record ID
    # shared across a recurrence, which needs the date appending.
    raw_id = occurrence.get("id")
    uid = (
        str(raw_id)
        if isinstance(raw_id, str) and ":" in raw_id
        else f"{occurrence.get('type')}-{raw_id}-{raw_date}"
    )
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
        minutes = (
            occurrence_field(occurrence, "duration_minutes") or DEFAULT_EVENT_MINUTES
        )
        end = start + timedelta(minutes=int(minutes))
    if end <= start:
        end = start + timedelta(minutes=DEFAULT_EVENT_MINUTES)

    return CalendarEvent(
        start=start, end=end, summary=title, description=description, uid=uid
    )


def distinct_names(people: list[dict[str, Any]]) -> list[str]:
    """Return one name per human, in order.

    The same person can reach an occurrence under two identities — as the
    `user` who owns it and as a `family_member` who participates in it. They
    are one human, so they are named once and counted once.
    """
    names: dict[str, str] = {}
    for person in people:
        name = person.get("name")
        if name:
            names.setdefault(str(name).casefold(), str(name))
    return list(names.values())


def _names(people: list[dict[str, Any]]) -> str:
    """Join people's names, one entry per human."""
    return ", ".join(distinct_names(people))


def occurrence_field(occurrence: dict[str, Any], name: str) -> Any:
    """Read a type-specific field from wherever this server puts it.

    The per-type endpoints carry these at the top level; `/schedule` gathers
    them under `details`. Either way the caller just asks for the field.
    """
    if (value := occurrence.get(name)) is not None:
        return value
    details = occurrence.get("details")
    return details.get(name) if isinstance(details, dict) else None


def source_type(occurrence: dict[str, Any]) -> str | None:
    """Return which Helm feature produced this occurrence."""
    source = occurrence.get("source")
    if isinstance(source, dict):
        value = source.get("type")
        return str(value) if value else None
    return str(source) if source else None


def source_record_id(occurrence: dict[str, Any]) -> Any:
    """Return the underlying record's own ID, which write endpoints take."""
    source = occurrence.get("source")
    return source.get("id") if isinstance(source, dict) else None


def _describe(occurrence: dict[str, Any]) -> str | None:
    """Build a readable description from the type-specific fields."""
    lines: list[str] = []
    if kind := occurrence.get("type"):
        lines.append(f"Type: {kind}")
    if meal_time := occurrence_field(occurrence, "meal_time"):
        lines.append(f"Meal: {meal_time}")
    if category := occurrence_field(occurrence, "category"):
        lines.append(f"Category: {category}")
    if subtype := occurrence_field(occurrence, "subtype"):
        lines.append(f"Subtype: {subtype}")
    if (duration := occurrence_field(occurrence, "duration_minutes")) is not None:
        lines.append(f"Duration: {duration} min")
    if (completed := occurrence_field(occurrence, "completed")) is not None:
        lines.append("Completed: yes" if completed else "Completed: no")
    if people := _names(occurrence_people(occurrence)):
        lines.append(f"Who: {people}")
    if url := occurrence_field(occurrence, "url"):
        lines.append(str(url))
    if source := source_type(occurrence):
        lines.append(f"Source: {source}")

    return "\n".join(lines) or None
