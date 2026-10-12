"""German public holidays, computed for any year (no yearly YAML upkeep)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Mapping

REGIONS = ("FEDERAL", "BY", "BE", "HH")


def easter_sunday(year: int) -> date:
    """Easter Sunday via the anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def german_holidays(year: int, region: str = "BY") -> dict[date, str]:
    """Return ``{date: name}`` for nationwide holidays plus the given region's extras.

    ``region`` is one of FEDERAL (nationwide only), BY (Bavaria), BE (Berlin), HH (Hamburg).
    Names are German on purpose: they are spoken to callers.
    """
    region = region.upper()
    if region not in REGIONS:
        raise ValueError(f"Unknown holiday region {region!r}; use one of {REGIONS}")

    easter = easter_sunday(year)
    days: dict[date, str] = {
        date(year, 1, 1): "Neujahr",
        easter - timedelta(days=2): "Karfreitag",
        easter + timedelta(days=1): "Ostermontag",
        date(year, 5, 1): "Tag der Arbeit",
        easter + timedelta(days=39): "Christi Himmelfahrt",
        easter + timedelta(days=50): "Pfingstmontag",
        date(year, 10, 3): "Tag der Deutschen Einheit",
        date(year, 12, 25): "1. Weihnachtstag",
        date(year, 12, 26): "2. Weihnachtstag",
    }
    if region == "BY":
        days.update({
            date(year, 1, 6): "Heilige Drei Könige",
            easter + timedelta(days=60): "Fronleichnam",
            date(year, 8, 15): "Mariä Himmelfahrt",
            date(year, 11, 1): "Allerheiligen",
        })
    elif region == "BE":
        days[date(year, 3, 8)] = "Internationaler Frauentag"
    elif region == "HH":
        days[date(year, 10, 31)] = "Reformationstag"
    return days


def is_holiday(
    day: date,
    region: str = "BY",
    extra: Mapping[date, str] | None = None,
) -> tuple[bool, str | None]:
    """Return ``(True, name)`` if ``day`` is a holiday or one of the clinic's own closed days."""
    if extra and day in extra:
        return True, extra[day]
    name = german_holidays(day.year, region).get(day)
    return (True, name) if name else (False, None)
