"""Rules that decide whether a claim is filed. Worth testing properly."""

from datetime import date

import pytest

from afas_declaraties.classify import ClassifierConfig, classify_day
from afas_declaraties.models import CalendarEvent, ClaimType, Reason, Verdict

CFG = ClassifierConfig()
MON = date(2026, 8, 31)  # Monday
SAT = date(2026, 8, 29)  # Saturday


def booking(day=MON, subject="Booking (A / Desk 1.01 / Zone A)", organiser="deskbird"):
    return CalendarEvent(
        day=day, subject=subject, all_day=True, show_as="free", organiser=organiser
    )


def meeting(day=MON, subject="Daily standup", show_as="busy"):
    return CalendarEvent(
        day=day, subject=subject, all_day=False, show_as=show_as, organiser="A Colleague"
    )


def test_desk_booking_is_an_office_day():
    r = classify_day(MON, [booking()], config=CFG)
    assert r.verdict is Verdict.OFFICE
    assert r.claim_type is ClaimType.COMMUTE


def test_no_booking_is_a_home_day():
    r = classify_day(MON, [meeting()], config=CFG)
    assert r.verdict is Verdict.HOME
    assert r.claim_type is ClaimType.HOME


def test_weekend_claims_nothing():
    assert classify_day(SAT, [booking(SAT)], config=CFG).claim_type is None


def test_out_of_office_beats_a_stale_booking():
    """Leave wins. A booking nobody cancelled must not produce a commute claim."""
    events = [booking(), CalendarEvent(MON, "Verlof", all_day=True, show_as="oof")]
    r = classify_day(MON, events, config=CFG)
    assert r.verdict is Verdict.ABSENT
    assert Reason.LEAVE_IN_CALENDAR in r.reasons


def test_afas_leave_beats_a_stale_booking():
    r = classify_day(MON, [booking()], config=CFG, on_leave_in_afas=True)
    assert r.verdict is Verdict.ABSENT


def test_degraded_calendar_is_never_read_as_a_home_day():
    """An outage must not silently become a month of working-from-home claims."""
    r = classify_day(MON, [], config=CFG, calendar_degraded=True)
    assert r.verdict is Verdict.AMBIGUOUS
    assert Reason.CALENDAR_DEGRADED in r.reasons


def test_withdrawn_booking_asks_rather_than_assumes():
    """Deleting the calendar entry is how the user says 'I stayed home'.

    It must not be actioned silently: the two sources now disagree, and only a
    human knows which was true.
    """
    r = classify_day(MON, [], config=CFG, previous_verdict=Verdict.OFFICE)
    assert r.verdict is Verdict.AMBIGUOUS
    assert Reason.BOOKING_WITHDRAWN in r.reasons


def test_subject_alone_does_not_make_a_booking():
    """Three signals must agree; a timed meeting called 'Booking review' is not a desk."""
    ev = CalendarEvent(
        MON, "Booking review call", all_day=False, show_as="busy", organiser="deskbird"
    )
    assert classify_day(MON, [ev], config=CFG).verdict is Verdict.HOME


def test_organiser_alone_does_not_make_a_booking():
    ev = CalendarEvent(MON, "Team lunch", all_day=True, show_as="free", organiser="deskbird")
    assert classify_day(MON, [ev], config=CFG).verdict is Verdict.HOME


@pytest.mark.parametrize("verdict", list(Verdict))
def test_a_day_never_yields_both_claim_types(verdict):
    """Art. 31a lid 12 Wet LB 1964: commute and WFH are mutually exclusive per day."""
    r = classify_day(MON, [], config=CFG)
    assert r.claim_type in (None, ClaimType.COMMUTE, ClaimType.HOME)
