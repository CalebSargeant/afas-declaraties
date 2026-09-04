"""Workflow-action selection.

The controls on a declaration task are banner links whose ids are
`P_R_W_<per-tenant-hex>_BannerItemLink_<n>`, and the index is ambiguous across
webparts: "_BannerItemLink_0" is BOTH "Declaratie insturen" and "Verwijderen".
Picking the wrong one deletes a draft instead of filing it, so the label is the
only safe discriminator and "exactly one match" is a hard requirement.
"""

from datetime import date

import pytest

from afas_declaraties.insite import (
    ACTION_DELETE,
    ACTION_SUBMIT,
    AmbiguousAction,
    ClaimLine,
    InSiteError,
    available_actions,
    find_workflow_action,
    verify_row,
)


class FakeElement:
    def __init__(self, text, ident="", visible=True):
        self._text, self._id, self._visible = text, ident, visible
        self.clicked = False

    def is_visible(self):
        return self._visible

    def inner_text(self):
        return self._text

    def get_attribute(self, name):
        return self._id if name == "id" else None

    def click(self):
        self.clicked = True


class FakePage:
    def __init__(self, elements):
        self._elements = elements

    def query_selector_all(self, _selector):
        return self._elements


# Shapes taken from the live portal, hex fabricated. What is asserted is that
# two distinct webparts both end in _BannerItemLink_0, so the trailing index
# cannot tell submit from delete.
ACTIONS = [
    FakeElement("Declaratie insturen", "P_R_W_AAAA0000AAAA0000AAAA0000AAAA0001_BannerItemLink_0"),
    FakeElement("Verwijderen", "P_R_W_BBBB1111BBBB1111BBBB1111BBBB0002_BannerItemLink_0"),
    FakeElement("Aanpassen", "P_R_W_BBBB1111BBBB1111BBBB1111BBBB0002_BannerItemLink_1"),
]


def test_submit_and_delete_are_told_apart():
    page = FakePage(ACTIONS)
    assert (
        find_workflow_action(page, ACTION_SUBMIT).get_attribute("id").startswith("P_R_W_AAAA0000")
    )
    assert (
        find_workflow_action(page, ACTION_DELETE).get_attribute("id").startswith("P_R_W_BBBB1111")
    )


def test_a_missing_action_refuses_rather_than_falling_through():
    """Returning the first link here would delete the draft."""
    with pytest.raises(AmbiguousAction):
        find_workflow_action(FakePage(ACTIONS), "Declaratie insturen ")  # trailing space


def test_duplicate_labels_refuse():
    dupes = ACTIONS + [FakeElement("Declaratie insturen", "P_R_W_OTHER_BannerItemLink_0")]
    with pytest.raises(AmbiguousAction):
        find_workflow_action(FakePage(dupes), ACTION_SUBMIT)


def test_invisible_controls_are_ignored():
    hidden = [FakeElement("Declaratie insturen", "hidden", visible=False)] + ACTIONS
    assert find_workflow_action(FakePage(hidden), ACTION_SUBMIT).get_attribute("id") != "hidden"


def test_available_actions_lists_only_visible_labels():
    page = FakePage(ACTIONS + [FakeElement("Geheim", "x", visible=False)])
    assert available_actions(page) == ["Declaratie insturen", "Verwijderen", "Aanpassen"]


def test_verify_row_rejects_a_period_that_does_not_match_the_date():
    """The prefilled period is the CURRENT month, so an unset period silently
    books a past-month day into the wrong payroll period."""
    line = ClaimLine(date(2026, 8, 13))
    with pytest.raises(InSiteError, match="period"):
        verify_row(
            {"Datum boeking": "13-08-2026", "Jaar": "2026", "ref:Periode": "9", "Aantal": "1,00"},
            line,
        )


def test_verify_row_accepts_a_correct_row():
    line = ClaimLine(date(2026, 8, 13))
    verify_row(
        {"Datum boeking": "13-08-2026", "Jaar": "2026", "ref:Periode": "8", "Aantal": "1,00"}, line
    )
