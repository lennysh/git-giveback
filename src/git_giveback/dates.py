"""Date range parsing and helpers.

Ranges are half-open: ``since <= t < until``. A plain ``--until`` date is
inclusive, so it is converted to midnight at the start of the following day.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Iterator

DEFAULT_DAYS = 30


def display_tz(utc: bool = False) -> tzinfo:
    if utc:
        return timezone.utc
    return datetime.now().astimezone().tzinfo or timezone.utc


def parse_when(value: str, tz: tzinfo, *, end: bool = False) -> datetime:
    """Parse ``YYYY-MM-DD`` or an ISO 8601 datetime into an aware datetime."""
    value = value.strip()
    if len(value) == 10:
        try:
            day = date.fromisoformat(value)
        except ValueError:
            pass
        else:
            moment = datetime.combine(day, time(), tz)
            return moment + timedelta(days=1) if end else moment
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid date '{value}': use YYYY-MM-DD or an ISO 8601 datetime"
        ) from None
    return moment if moment.tzinfo else moment.replace(tzinfo=tz)


def resolve_range(
    since: str | None,
    until: str | None,
    days: int | None,
    tz: tzinfo,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    end = parse_when(until, tz, end=True) if until else (now or datetime.now(tz))
    if since:
        start = parse_when(since, tz)
    else:
        start = end - timedelta(days=days if days is not None else DEFAULT_DAYS)
    if start >= end:
        raise argparse.ArgumentTypeError("--since must be earlier than --until")
    return start, end


def chunks(start: datetime, end: datetime, days: int) -> Iterator[tuple[datetime, datetime]]:
    """Split [start, end) into consecutive windows of at most ``days`` days."""
    step = timedelta(days=days)
    cursor = start
    while cursor < end:
        nxt = min(cursor + step, end)
        yield cursor, nxt
        cursor = nxt


def to_utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_api_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def in_range(moment: datetime | None, start: datetime, end: datetime) -> bool:
    return moment is not None and start <= moment < end


def format_range(start: datetime, end: datetime, tz: tzinfo) -> str:
    s, e = start.astimezone(tz), end.astimezone(tz)
    if s.time() == time() and e.time() == time():
        text = f"{s.date()} to {(e - timedelta(days=1)).date()}"
    else:
        text = f"{s:%Y-%m-%d %H:%M} to {e:%Y-%m-%d %H:%M}"
    return f"{text} ({s.tzname()})"
