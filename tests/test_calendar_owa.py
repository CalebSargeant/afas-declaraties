"""Parsing OWA aria-labels.

Fixtures reproduce the real label STRUCTURE exactly; every value in them is
invented. The parser reads structure only, so the tests are unaffected and no
desk, zone, colleague or team name from the source calendar is reproduced here.
"""

from datetime import date

import pytest

from afas_declaraties.calendar_owa import LabelParseError, parse_event_label
from afas_declaraties.classify import ClassifierConfig

CFG = ClassifierConfig()

# Modelled on real aria-labels: the STRUCTURE is verbatim (delimiters, comma
# placement, the trailing "By <organiser>, <ShowAs>" suffix), while every
# value is invented. The parser only reads the structure, so nothing is lost
# and no desk, zone, colleague or team name from the source calendar appears.
BOOKING_WITH_HOURS = (
    "Booking (A / Desk 1.01 / Zone A / 08:00 - 17:00), all day event, "
    "Monday, August 31, 2026, By deskbird, Free"
)
BOOKING_NO_HOURS = (
    "Booking (B / Desk 1.02 / Zone A), all day event, "
    "Thursday, September 3, 2026, By deskbird, Free"
)
RECURRING_MEETING = (
    "!! Daily standup Team Alpha & Beta !!, 8:45 AM to 9:30 AM, "
    "Monday, August 31, 2026, By A Colleague, Busy, Recurring event"
)
NAV_LABEL = "August 31 – September 4, 2026, Jump to a specific date or date range."


def test_parses_a_desk_booking():
    e = parse_event_label(BOOKING_WITH_HOURS)
    assert e.day == date(2026, 8, 31)
    assert e.subject == "Booking (A / Desk 1.01 / Zone A / 08:00 - 17:00)"
    assert e.all_day is True
    assert e.organiser == "deskbird"
    assert e.show_as == "free"
    assert e.is_desk_booking(CFG.booking_organisers, CFG.booking_subject_prefixes)


def test_parses_a_booking_without_an_hours_suffix():
    e = parse_event_label(BOOKING_NO_HOURS)
    assert e.day == date(2026, 9, 3)
    assert e.is_desk_booking(CFG.booking_organisers, CFG.booking_subject_prefixes)


def test_subject_containing_commas_and_punctuation_survives():
    e = parse_event_label(RECURRING_MEETING)
    assert e.subject == "!! Daily standup Team Alpha & Beta !!"
    assert e.all_day is False
    assert e.show_as == "busy"
    assert not e.is_desk_booking(CFG.booking_organisers, CFG.booking_subject_prefixes)


def test_out_of_office_is_detected():
    e = parse_event_label("Verlof, all day event, Tuesday, September 1, 2026, By Me, Away")
    assert e.is_out_of_office


def test_navigation_chrome_is_parsed_but_is_not_a_booking():
    """The date-picker label carries a date, so it parses; it must not look like a desk."""
    e = parse_event_label(NAV_LABEL)
    assert not e.is_desk_booking(CFG.booking_organisers, CFG.booking_subject_prefixes)


def test_a_label_with_no_date_is_rejected_loudly():
    with pytest.raises(LabelParseError):
        parse_event_label("Some button with no date at all")
