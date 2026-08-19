"""Guards on how many API requests the integration makes.

Helm allows 60 requests/minute per credential. Opening a calendar view asks
every calendar entity for the same window at once, so duplicate work has to be
collapsed or the limit is reached immediately.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import setup_helm

RATE_LIMIT_PER_MINUTE = 60


def _planning_requests(aioclient_mock: AiohttpClientMocker) -> list[str]:
    """Return the planning endpoint calls made so far."""
    endpoints = ("/meals", "/exercises", "/events", "/chores", "/habits", "/schedule")
    return [
        call[1].path
        for call in aioclient_mock.mock_calls
        if call[0].lower() == "get" and call[1].path.endswith(endpoints)
    ]


async def test_month_view_across_every_calendar_stays_in_budget(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The calendar panel opening must not exhaust the rate limit.

    Ten calendars each needing five planning types over a 42-day window is 60
    naive requests. Identical fetches are shared, so only the unique ones go
    out: five types x two chunks.
    """
    today = await setup_helm(hass, aioclient_mock, config_entry)

    calendars = [
        state.entity_id
        for state in hass.states.async_all("calendar")
        if state.entity_id.startswith("calendar.helm_")
    ]
    assert len(calendars) >= 10, calendars

    aioclient_mock.mock_calls.clear()

    # A month grid, which exceeds the API's 31-day limit and so is chunked.
    start = dt_util.start_of_local_day(today - timedelta(days=7))
    end = start + timedelta(days=42)

    await asyncio.gather(
        *(
            hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": entity_id,
                    "start_date_time": start.isoformat(),
                    "end_date_time": end.isoformat(),
                },
                blocking=True,
                return_response=True,
            )
            for entity_id in calendars
        )
    )

    requests = _planning_requests(aioclient_mock)
    assert len(requests) <= RATE_LIMIT_PER_MINUTE, (
        f"{len(requests)} requests for {len(calendars)} calendars exceeds the "
        f"{RATE_LIMIT_PER_MINUTE}/minute limit"
    )
    # Five planning types, each split into two chunks for a 42-day range.
    assert len(requests) == 10, requests


async def test_repeat_navigation_is_served_from_cache(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Asking for the same window again shortly after costs nothing."""
    today = await setup_helm(hass, aioclient_mock, config_entry)
    start = dt_util.start_of_local_day(today + timedelta(days=60))
    end = start + timedelta(days=20)

    async def fetch() -> None:
        await hass.services.async_call(
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

    await fetch()
    aioclient_mock.mock_calls.clear()
    await fetch()

    assert _planning_requests(aioclient_mock) == []


async def test_cached_window_needs_no_requests_at_all(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Today's view sits inside the polled window, so it is free."""
    today = await setup_helm(hass, aioclient_mock, config_entry)
    aioclient_mock.mock_calls.clear()

    start = dt_util.start_of_local_day(today)
    for entity_id in ("calendar.helm_schedule", "calendar.helm_luke"):
        await hass.services.async_call(
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

    assert _planning_requests(aioclient_mock) == []


async def test_concurrent_callers_share_one_fetch(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Callers that arrive while a fetch is in flight get the same result.

    The mocked API answers instantly, which would let every caller hit the
    result cache and never exercise the in-flight path. Holding the first
    fetch open forces genuine overlap, the way real network latency does.
    """
    today = await setup_helm(hass, aioclient_mock, config_entry)
    coordinator = config_entry.runtime_data.planning

    release = asyncio.Event()
    calls = 0
    real_get_planning = coordinator.client.async_get_planning

    async def slow_get_planning(endpoint, start, end):
        nonlocal calls
        calls += 1
        await release.wait()
        return await real_get_planning(endpoint, start, end)

    coordinator.client.async_get_planning = slow_get_planning

    # Well outside the polled window, so it genuinely has to fetch.
    start = today + timedelta(days=90)
    end = start + timedelta(days=10)

    waiters = [
        asyncio.create_task(coordinator.async_fetch_range(start, end, ["chore"]))
        for _ in range(5)
    ]
    # asyncio.gather wraps each fetch in a task of its own, so a single yield
    # is not enough for the waiters to reach the in-flight branch. Pump the
    # loop until the fetch is registered and every waiter is parked on it.
    for _ in range(50):
        await asyncio.sleep(0)
        if coordinator._range_requests and not any(w.done() for w in waiters):
            break
    assert coordinator._range_requests, "no fetch was registered as in flight"

    release.set()
    results = await asyncio.gather(*waiters)

    assert calls == 1, f"{calls} fetches for 5 concurrent callers"
    # Every caller gets a real occurrence list, not the raw (data, meta) tuple.
    for result in results:
        assert isinstance(result, list)
        assert all(isinstance(item, dict) for item in result)
    assert results[0] == results[-1]
