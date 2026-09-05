"""Reading which week is on screen.

Every navigation decision is made against this, so getting it wrong does not
fail loudly -- it reads a different week's events and classifies real office
days as working-from-home.
"""

from datetime import date

from afas_declaraties.calendar_owa import looks_like_event, rendered_week


class FakePage:
    """Stands in for a Playwright page; rendered_week only harvests labels."""

    def __init__(self, labels):
        self._labels = labels
        self.waits = 0

    def evaluate(self, _js):
        return self._labels

    def wait_for_timeout(self, _ms):
        # rendered_week retries: the header re-renders asynchronously after a
        # week change, so a single read can catch the page mid-update.
        self.waits += 1


# Verbatim label SHAPES from the live calendar. The navigation buttons describe
# the week they would move TO, so they carry a date range that is not the one on
# screen. Order matters: the nav button appears before the header in the DOM.
NAV_PREV = "Go to previous week \nAugust 24 – 28, 2026"
NAV_NEXT = "Go to next week \nSeptember 7 – 11, 2026"
HEADER = "August 31 – September 4, 2026, Jump to a specific date or date range."
EVENT = (
    "Booking (A / Desk 1.01 / Zone A), all day event, Monday, August 31, 2026, By deskbird, Free"
)


def test_header_wins_over_the_navigation_buttons():
    """The regression: matching the first range on the page read the PREVIOUS
    week, so the reader navigated from a baseline one week out and returned a
    different week's events under the requested week's name."""
    assert rendered_week(FakePage([NAV_PREV, NAV_NEXT, HEADER, EVENT])) == (
        date(2026, 8, 31),
        date(2026, 9, 4),
    )


def test_navigation_buttons_alone_are_not_treated_as_the_current_week():
    assert rendered_week(FakePage([NAV_PREV, NAV_NEXT])) is None


def test_same_month_range_parses():
    page = FakePage(["August 24 – 28, 2026, Jump to a specific date or date range."])
    assert rendered_week(page) == (date(2026, 8, 24), date(2026, 8, 28))


def test_returns_none_when_no_header_is_present():
    """Better to report "cannot tell" than to guess a week."""
    page = FakePage([EVENT])
    assert rendered_week(page) is None
    assert page.waits > 0, "should retry before giving up on the header"


def test_looks_like_event_distinguishes_events_from_chrome():
    assert looks_like_event(EVENT)
    for chrome in (NAV_PREV, NAV_NEXT, HEADER, "Go to today"):
        assert not looks_like_event(chrome), chrome
