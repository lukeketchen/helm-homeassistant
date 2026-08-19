"""Data update coordinators for the Helm integration."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, tzinfo
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    HelmAbilityError,
    HelmAuthError,
    HelmClient,
    HelmError,
    HelmRateLimitError,
)
from .const import (
    CONF_DAYS_AHEAD,
    CONF_DAYS_PAST,
    CONF_UPDATE_INTERVAL,
    DEFAULT_DAYS_AHEAD,
    DEFAULT_DAYS_PAST,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLANNING_ENDPOINTS,
)

_LOGGER = logging.getLogger(__name__)

# How long an arbitrary-range result is reused. Long enough to collapse the
# burst of requests the calendar panel makes when it opens.
RANGE_CACHE_TTL = 30.0


def _update_interval(entry: ConfigEntry) -> timedelta:
    """Return the poll interval configured for this entry."""
    minutes = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    return timedelta(minutes=max(1, int(minutes)))


async def _resolve_timezone(hass: HomeAssistant, name: str | None) -> tzinfo | None:
    """Resolve an IANA name to a tzinfo without blocking the event loop."""
    if not name:
        return None
    if hasattr(dt_util, "async_get_time_zone"):
        return await dt_util.async_get_time_zone(name)
    return await hass.async_add_executor_job(dt_util.get_time_zone, name)


class HelmPlanningCoordinator(DataUpdateCoordinator[dict[str, list[dict[str, Any]]]]):
    """Keeps the rolling planning window fresh for calendars and sensors."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: HelmClient
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} planning",
            update_interval=_update_interval(entry),
            config_entry=entry,
        )
        self.client = client
        self.timezone_name: str | None = None
        self.timezone: tzinfo | None = None
        self.window_start: date | None = None
        self.window_end: date | None = None
        # (type, start, end) -> (timestamp, occurrences), and in-flight fetches.
        self._range_cache: dict[
            tuple[str, date, date], tuple[float, list[dict[str, Any]]]
        ] = {}
        self._range_requests: dict[tuple[str, date, date], Any] = {}

    @property
    def days_past(self) -> int:
        """Days of history to keep loaded."""
        return int(self.config_entry.options.get(CONF_DAYS_PAST, DEFAULT_DAYS_PAST))

    @property
    def days_ahead(self) -> int:
        """Days of look-ahead to keep loaded."""
        return int(self.config_entry.options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD))

    def now(self) -> datetime:
        """Return 'now' in the token owner's timezone when it is known."""
        return dt_util.now(self.timezone)

    def today(self) -> date:
        """Return today's date in the token owner's timezone."""
        return self.now().date()

    def covers(self, start: date, end: date) -> bool:
        """Return True when the cached window already spans this range."""
        if self.window_start is None or self.window_end is None:
            return False
        return self.window_start <= start and end <= self.window_end

    def occurrences(self, planning_type: str) -> list[dict[str, Any]]:
        """Return cached occurrences for one planning type."""
        return (self.data or {}).get(planning_type, [])

    def all_occurrences(self) -> list[dict[str, Any]]:
        """Return every cached occurrence, sorted the way /schedule sorts."""
        merged = [item for items in (self.data or {}).values() for item in items]
        return sorted(merged, key=_sort_key)

    async def async_set_timezone(self, name: str | None) -> None:
        """Seed the household timezone, normally from /me before the first poll."""
        if not name or name == self.timezone_name:
            return
        self.timezone_name = name
        self.timezone = await _resolve_timezone(self.hass, name)

    async def _async_update_data(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch each planning endpoint for the configured window."""
        today = self.today()
        start = today - timedelta(days=self.days_past)
        end = today + timedelta(days=self.days_ahead)

        try:
            results = await asyncio.gather(
                *(
                    self.client.async_get_planning(endpoint, start, end)
                    for endpoint in PLANNING_ENDPOINTS.values()
                )
            )
        except HelmAuthError as err:
            raise ConfigEntryAuthFailed(err.message) from err
        except HelmAbilityError as err:
            raise ConfigEntryAuthFailed(err.message) from err
        except HelmRateLimitError as err:
            raise UpdateFailed(
                f"Rate limited by Helm; retry after {err.retry_after or 60}s"
            ) from err
        except HelmError as err:
            raise UpdateFailed(err.message) from err

        data: dict[str, list[dict[str, Any]]] = {}
        for planning_type, (occurrences, meta) in zip(
            PLANNING_ENDPOINTS, results, strict=True
        ):
            data[planning_type] = occurrences
            await self.async_set_timezone(meta.get("timezone"))

        self.window_start = start
        self.window_end = end
        return data

    async def _async_fetch_type(
        self, kind: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        """Fetch one planning type for a range, sharing work between callers.

        Opening the calendar panel asks every calendar entity for the same
        window at once, and the per-person calendars all need identical data.
        Without this, ten entities would each fetch all five types and blow
        through the API's 60 requests/minute. Identical fetches are collapsed
        into one, and the result is briefly reused.
        """
        key = (kind, start, end)
        now = self.hass.loop.time()

        cached = self._range_cache.get(key)
        if cached is not None and now - cached[0] < RANGE_CACHE_TTL:
            return cached[1]

        if (existing := self._range_requests.get(key)) is not None:
            # An identical fetch is already in flight; wait for that one.
            return await existing

        # The task must resolve to the occurrences alone: callers waiting on an
        # in-flight fetch get exactly what the originating caller gets.
        async def _fetch() -> list[dict[str, Any]]:
            occurrences, _meta = await self.client.async_get_planning(
                PLANNING_ENDPOINTS[kind], start, end
            )
            return occurrences

        task = self.hass.async_create_task(
            _fetch(), name=f"{DOMAIN} fetch {kind} {start}..{end}"
        )
        self._range_requests[key] = task
        try:
            occurrences = await task
        finally:
            self._range_requests.pop(key, None)

        self._prune_range_cache(now)
        self._range_cache[key] = (now, occurrences)
        return occurrences

    def _prune_range_cache(self, now: float) -> None:
        """Drop range results that are past their short lifetime."""
        stale = [
            key
            for key, (stamp, _) in self._range_cache.items()
            if now - stamp >= RANGE_CACHE_TTL
        ]
        for key in stale:
            del self._range_cache[key]

    async def async_fetch_range(
        self, start: date, end: date, planning_types: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Fetch an arbitrary range, served from cache when it is covered."""
        wanted = planning_types or list(PLANNING_ENDPOINTS)

        if self.covers(start, end):
            source = [item for kind in wanted for item in self.occurrences(kind)]
        else:
            results = await asyncio.gather(
                *(self._async_fetch_type(kind, start, end) for kind in wanted)
            )
            source = [item for occurrences in results for item in occurrences]

        in_range = [
            item
            for item in source
            if (day := _occurrence_date(item)) is not None and start <= day <= end
        ]
        return sorted(in_range, key=_sort_key)


class HelmShoppingCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Keeps the shared shopping list in sync."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: HelmClient
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} shopping list",
            update_interval=_update_interval(entry),
            config_entry=entry,
        )
        self.client = client

    async def _async_update_data(self) -> list[dict[str, Any]]:
        """Fetch the whole list; it has no pagination or date filter."""
        try:
            return await self.client.async_get_shopping_items()
        except HelmAuthError as err:
            raise ConfigEntryAuthFailed(err.message) from err
        except HelmAbilityError as err:
            raise ConfigEntryAuthFailed(err.message) from err
        except HelmRateLimitError as err:
            raise UpdateFailed(
                f"Rate limited by Helm; retry after {err.retry_after or 60}s"
            ) from err
        except HelmError as err:
            raise UpdateFailed(err.message) from err


def _occurrence_date(occurrence: dict[str, Any]) -> date | None:
    """Return the day an occurrence falls on."""
    raw = occurrence.get("date")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _sort_key(occurrence: dict[str, Any]) -> tuple[str, int, str, str]:
    """Sort all-day entries first within a day, then by start time and title."""
    day = occurrence.get("date") or ""
    starts_at = occurrence.get("starts_at")
    return (
        day,
        0 if not starts_at else 1,
        starts_at or "",
        occurrence.get("title") or "",
    )
