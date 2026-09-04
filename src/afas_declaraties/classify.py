"""Turning evidence into a claim decision for a single day.

The rules here are deliberately asymmetric. An office day needs positive
evidence; home is the residual; and anything contradictory becomes a question
for a human rather than a guess. The failure direction is always to claim less,
because an omitted day costs a couple of euros and a wrong day is a false
expense claim.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from .models import (
    CalendarEvent,
    DayClassification,
    Reason,
    Verdict,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClassifierConfig:
    """Everything tenant- or person-specific, so nothing is a constant."""

    #: Calendar organisers that mark a desk booking (e.g. the booking tool).
    booking_organisers: tuple[str, ...] = ("deskbird",)
    #: Subject prefixes for a desk booking, matched case-insensitively.
    booking_subject_prefixes: tuple[str, ...] = ("booking",)
    #: Weekdays that count as working days. Monday is 0.
    working_weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    #: Dates never claimed (public holidays, contractual non-working days).
    excluded_dates: frozenset[date] = frozenset()


def classify_day(
    day: date,
    events: Iterable[CalendarEvent],
    *,
    config: ClassifierConfig,
    on_leave_in_afas: bool = False,
    calendar_degraded: bool = False,
    previous_verdict: Verdict | None = None,
) -> DayClassification:
    """Decide what, if anything, to claim for ``day``.

    ``previous_verdict`` is what a prior run froze for this date. Supplying it
    lets the reconciliation rule fire: if a day was frozen as office and the
    booking has since disappeared from the calendar, that is a deliberate
    correction by the user, not evidence of a home day -- but it is also not
    something to action silently, so it becomes a question.
    """
    events = list(events)
    result = DayClassification(
        day=day,
        verdict=Verdict.ABSENT,
        evidence={
            "event_count": len(events),
            "previous_verdict": previous_verdict.value if previous_verdict else None,
        },
    )

    # Non-working days first: cheap, and they short-circuit everything else.
    if day.weekday() not in config.working_weekdays:
        result.reasons.append(Reason.WEEKEND)
        return result
    if day in config.excluded_dates:
        result.verdict = Verdict.ABSENT
        result.reasons.append(Reason.PUBLIC_HOLIDAY)
        return result

    # A degraded feed must never be read as "no bookings, therefore home". That
    # would quietly convert an outage into a month of wrong claims.
    if calendar_degraded:
        result.verdict = Verdict.AMBIGUOUS
        result.reasons.append(Reason.CALENDAR_DEGRADED)
        return result

    if on_leave_in_afas:
        result.verdict = Verdict.ABSENT
        result.reasons.append(Reason.LEAVE_IN_AFAS)
        return result

    if any(e.is_out_of_office for e in events):
        result.verdict = Verdict.ABSENT
        result.reasons.append(Reason.LEAVE_IN_CALENDAR)
        return result

    bookings = [
        e
        for e in events
        if e.is_desk_booking(config.booking_organisers, config.booking_subject_prefixes)
    ]
    result.evidence["bookings"] = [e.subject for e in bookings]

    if bookings:
        result.verdict = Verdict.OFFICE
        result.reasons.append(Reason.BOOKING_PRESENT)
        return result

    # No booking now. If a previous run froze this as an office day, the two
    # sources disagree and the user is the tie-breaker -- deleting the calendar
    # entry is how they say "I did not actually go in", and that signal must
    # not be lost, but nor should it be assumed.
    if previous_verdict is Verdict.OFFICE:
        result.verdict = Verdict.AMBIGUOUS
        result.reasons.append(Reason.BOOKING_WITHDRAWN)
        return result

    result.verdict = Verdict.HOME
    result.reasons.append(Reason.NO_BOOKING)
    return result
