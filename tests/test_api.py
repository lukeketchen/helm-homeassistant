"""Tests for the pure logic in the Helm API client."""

from __future__ import annotations

from datetime import date
from itertools import pairwise

from custom_components.helm.api import date_chunks


def test_short_range_is_one_chunk() -> None:
    """A week fits in a single request."""
    chunks = list(date_chunks(date(2026, 8, 18), date(2026, 8, 24)))
    assert chunks == [(date(2026, 8, 18), date(2026, 8, 24))]


def test_single_day() -> None:
    """A one day range is still one chunk."""
    assert list(date_chunks(date(2026, 8, 18), date(2026, 8, 18))) == [
        (date(2026, 8, 18), date(2026, 8, 18))
    ]


def test_month_grid_is_two_chunks() -> None:
    """A 42 day calendar grid splits into two requests, none over 31 days."""
    chunks = list(date_chunks(date(2026, 8, 1), date(2026, 9, 11)))
    assert len(chunks) == 2
    assert chunks[0] == (date(2026, 8, 1), date(2026, 8, 31))
    assert chunks[1] == (date(2026, 9, 1), date(2026, 9, 11))
    for start, end in chunks:
        assert (end - start).days + 1 <= 31


def test_chunks_are_contiguous_and_cover_the_range() -> None:
    """Chunks tile the range with no gaps or overlaps."""
    start, end = date(2026, 1, 1), date(2026, 6, 30)
    chunks = list(date_chunks(start, end))
    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    for (_, previous_end), (next_start, _) in pairwise(chunks):
        assert (next_start - previous_end).days == 1


def test_reversed_range_is_normalised() -> None:
    """Passing the dates the wrong way round still yields a valid range."""
    assert list(date_chunks(date(2026, 8, 24), date(2026, 8, 18))) == [
        (date(2026, 8, 18), date(2026, 8, 24))
    ]
