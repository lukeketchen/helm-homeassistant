"""Calendar entities for Helm planning data."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DEFAULT_EVENT_MINUTES, PLANNING_TYPES
from .coordinator import HelmPlanningCoordinator
from .entity import HelmEntity

if TYPE_CHECKING:
    from . import HelmConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HelmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one calendar per planning type, plus a merged one."""
    coordinator = entry.runtime_data.planning
    if coordinator is None:
        return

    entities: list[HelmCalendarEntity] = [
        HelmCalendarEntity(coordinator, entry, planning_type)
        for planning_type in PLANNING_TYPES
    ]
    entities.append(HelmCalendarEntity(coordinator, entry, None))
    async_add_entities(entities)


class HelmCalendarEntity(HelmEntity, CalendarEntity):
    """A calendar backed by one planning type, or by all of them."""

    coordinator: HelmPlanningCoordinator

    def __init__(
        self,
        coordinator: HelmPlanningCoordinator,
        entry: HelmConfigEntry,
        planning_type: str | None,
    ) -> None:
        """Initialise the calendar."""
        key = planning_type or "schedule"
        super().__init__(coordinator, entry, f"calendar_{key}")
        self._planning_type = planning_type
        self._attr_translation_key = f"calendar_{key}"

    @property
    def _types(self) -> list[str]:
        """Return the planning types this calendar covers."""
        return [self._planning_type] if self._planning_type else list(PLANNING_TYPES)

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

    def _cached_occurrences(self) -> list[dict[str, Any]]:
        """Return the coordinator's cached occurrences for this calendar."""
        if self._planning_type:
            return self.coordinator.occurrences(self._planning_type)
        return self.coordinator.all_occurrences()

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return events in a range, fetching beyond the cached window."""
        tz = self.coordinator.timezone or dt_util.DEFAULT_TIME_ZONE
        start = start_date.astimezone(tz).date()
        end = end_date.astimezone(tz).date()

        occurrences = await self.coordinator.async_fetch_range(start, end, self._types)
        events = [event for occ in occurrences if (event := _to_calendar_event(occ))]
        return [event for event in events if _overlaps(event, start_date, end_date)]


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


def _names(people: Any) -> str:
    """Join participant names from a Helm people array."""
    if not isinstance(people, list):
        return ""
    return ", ".join(
        person["name"]
        for person in people
        if isinstance(person, dict) and person.get("name")
    )


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
    owner = occurrence.get("owner")
    if isinstance(owner, dict) and owner.get("name"):
        lines.append(f"Owner: {owner['name']}")
    if people := _names(occurrence.get("assignees") or occurrence.get("participants")):
        lines.append(f"Who: {people}")
    if details := occurrence.get("details"):
        lines.append(str(details))
    if url := occurrence.get("url"):
        lines.append(str(url))
    if source := occurrence.get("source"):
        lines.append(f"Source: {source}")

    return "\n".join(lines) or None
