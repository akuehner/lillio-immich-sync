"""Built-in schedule for the long-running `daemon` command: run at fixed local times on chosen weekdays."""
from __future__ import annotations

from datetime import datetime, time, timedelta

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ALIASES = {"daily": DAY_NAMES, "weekdays": DAY_NAMES[:5], "weekends": DAY_NAMES[5:]}


def parse_times(text: str) -> tuple[time, ...]:
    """'18:00' or '07:30,18:00' -> sorted times. Raises ValueError on bad input."""
    out = set()
    for part in text.split(","):
        part = part.strip()
        if part:
            hh, mm = part.split(":")
            out.add(time(int(hh), int(mm)))
    if not out:
        raise ValueError("no times given")
    return tuple(sorted(out))


def parse_days(text: str) -> frozenset[int]:
    """'daily', 'weekdays', 'weekends', 'mon-fri' or 'mon,wed,fri' -> weekday numbers (Monday = 0)."""
    out: set[int] = set()
    for part in text.lower().split(","):
        part = part.strip()
        if not part:
            continue
        if part in ALIASES:
            out |= {DAY_NAMES.index(d) for d in ALIASES[part]}
        elif "-" in part:
            a, b = (DAY_NAMES.index(x.strip()[:3]) for x in part.split("-", 1))
            out |= {d % 7 for d in range(a, a + ((b - a) % 7) + 1)}
        else:
            out.add(DAY_NAMES.index(part[:3]))
    if not out:
        raise ValueError("no days given")
    return frozenset(out)


def next_run(now: datetime, times: tuple[time, ...], days: frozenset[int]) -> datetime:
    """First scheduled moment strictly after `now` (timezone-aware; result in now's zone)."""
    tz = now.tzinfo
    for offset in range(8):
        day = (now + timedelta(days=offset)).date()
        if day.weekday() not in days:
            continue
        for t in times:
            candidate = datetime.combine(day, t, tzinfo=tz)
            if candidate > now:
                return candidate
    raise ValueError("schedule has no run times")  # unreachable with non-empty times and days
