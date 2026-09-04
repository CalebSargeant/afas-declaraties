"""Reading the work calendar out of Outlook Web.

Why scrape rather than call an API: this tenant does not grant app
registrations, so Microsoft Graph is unavailable. The alternatives were a
published-ICS capability URL (world-readable, and it silently truncates to a
rolling three-month window) or borrowing the access token OWA mints in-page for
its own first-party client -- which is the token-replay shape security tooling
is built to flag. Driving the page as the signed-in user is the honest option
and reuses the Entra session the job already holds.

OWA puts the whole event description in each element's ``aria-label``:

    Booking (A / Desk 1.01 / Zone A), all day event, Monday, August 31, 2026, By deskbird, Free
    Daily standup, 8:45 AM to 9:30 AM, Monday, August 31, 2026, By A Colleague, Busy, Recurring event

Parsing is kept separate from the browser so the fragile half is unit-testable
against captured strings, with no login required.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from .models import CalendarEvent

logger = logging.getLogger(__name__)

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

_DATE_RE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),\s*(\d{4})\b", re.IGNORECASE)
_ORGANISER_RE = re.compile(r",\s*By\s+([^,]+)")
_SHOW_AS_RE = re.compile(r",\s*(Free|Busy|Tentative|Away|Working elsewhere)\b", re.IGNORECASE)
_ALL_DAY_RE = re.compile(r",\s*all day event\b", re.IGNORECASE)
_TIME_RE = re.compile(r",\s*\d{1,2}:\d{2}\s*(?:AM|PM)?\s+to\s+", re.IGNORECASE)

_SHOW_AS_MAP = {
    "free": "free",
    "busy": "busy",
    "tentative": "tentative",
    "away": "oof",
    "working elsewhere": "busy",
}


class LabelParseError(ValueError):
    """An aria-label did not look like an event."""


def parse_event_label(label: str) -> CalendarEvent:
    """Turn one OWA ``aria-label`` into a :class:`CalendarEvent`.

    Parsed from the right-hand side. The subject is whatever precedes the
    time-or-all-day marker, because a subject may legitimately contain commas
    and splitting on them loses events with punctuation in the title.
    """
    m = _DATE_RE.search(label)
    if not m:
        raise LabelParseError(f"no date found in {label[:80]!r}")
    day = date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))

    all_day = bool(_ALL_DAY_RE.search(label))
    marker = _ALL_DAY_RE.search(label) or _TIME_RE.search(label)
    subject = label[: marker.start()].strip() if marker else label[: m.start()].rstrip(", ").strip()

    organiser_m = _ORGANISER_RE.search(label)
    show_as_m = _SHOW_AS_RE.search(label)
    show_as = _SHOW_AS_MAP.get(show_as_m.group(1).lower(), "busy") if show_as_m else "busy"

    return CalendarEvent(
        day=day,
        subject=subject,
        all_day=all_day,
        show_as=show_as,
        organiser=organiser_m.group(1).strip() if organiser_m else "",
    )


#: Collects every element carrying a descriptive aria-label. OWA renders events
#: as buttons/gridcells; harvesting broadly and filtering by parseability is
#: more durable than depending on one component's class name.
_HARVEST_JS = """() => {
  const out = new Set();
  for (const el of document.querySelectorAll('[role="button"],[role="gridcell"],[aria-label]')) {
    const a = el.getAttribute('aria-label') || '';
    if (a.length > 18 && /\\d{4}/.test(a)) out.add(a);
  }
  return [...out];
}"""


def read_week(page, monday: date, *, settle_ms: int = 12_000) -> tuple[list[CalendarEvent], bool]:
    """Read one work week from OWA. Returns ``(events, degraded)``.

    ``degraded`` is the important half of the return value. "No events found"
    and "the page did not load" look identical downstream, and treating the
    second as the first turns an outage into a month of home-working claims.
    """
    url = f"https://outlook.office.com/calendar/view/workweek?startdate={monday.isoformat()}"
    page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(settle_ms)

    labels = page.evaluate(_HARVEST_JS)
    events: list[CalendarEvent] = []
    failures = 0
    for label in labels:
        try:
            events.append(parse_event_label(label))
        except LabelParseError:
            failures += 1

    week = {monday + timedelta(days=i) for i in range(7)}
    in_week = [e for e in events if e.day in week]

    # Every harvested label failing to parse means the label format moved, not
    # that the week was empty. Distinguishing the two is the whole point.
    degraded = bool(labels) and not in_week and failures == len(labels)
    if degraded:
        logger.error(
            "owa: %d labels harvested, none parsed -- label format may have changed", failures
        )
    elif not in_week:
        logger.warning("owa: no events for week of %s", monday)

    logger.info(
        "owa: week %s -> %d events (%d labels, %d unparsed)",
        monday,
        len(in_week),
        len(labels),
        failures,
    )
    return in_week, degraded
