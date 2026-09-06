"""Which period `submit` files.

The submit job runs on a timer but must never work its period out from the
date. `build` runs on the 28th and defaults to the *previous* month, so a run
that lands on the 1st and asks "what was last month?" names the month after
the one the human approved. AFAS has no undo, so the period comes from the
approved row and nowhere else.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from afas_declaraties import cli
from afas_declaraties.models import ClaimType


def submission(year, period, claim_type, digest="d0", state="approved"):
    return {
        "period_year": year,
        "period_no": period,
        "claim_type": claim_type.value,
        "lines_digest": digest,
        "state": state,
    }


class FakeStore:
    """Just enough of `store` for cmd_submit, recording what it was told."""

    def __init__(self, approved, days=None, digest="d0"):
        self._approved = approved
        self._days = days if days is not None else [date(2026, 8, 27)]
        self._digest = digest
        self.states: list[tuple] = []
        self.marked: list[list[date]] = []
        self.claimable_calls: list[tuple] = []

    @contextmanager
    def connect(self, dsn):
        yield object()

    def ensure_schema(self, conn):
        pass

    def approved_submissions(self, conn):
        return list(self._approved)

    def get_submission(self, conn, year, period, claim_type):
        for row in self._approved:
            if (row["period_year"], row["period_no"], row["claim_type"]) == (
                year,
                period,
                claim_type.value,
            ):
                return row
        return None

    def claimable_days(self, conn, year, period, claim_type):
        self.claimable_calls.append((year, period, claim_type))
        return [{"day": d} for d in self._days]

    def lines_digest(self, days, claim_type):
        return self._digest

    @contextmanager
    def browser_lock(self, conn):
        yield True

    def set_submission_state(self, conn, year, period, claim_type, state, error=None):
        self.states.append((year, period, claim_type, state, error))

    def mark_days_state(self, conn, days, state):
        self.marked.append(list(days))


@pytest.fixture
def cfg():
    return SimpleNamespace(
        database_url="postgresql://x/y",
        commute_page="forms/woon-werk",
        home_page="forms/thuiswerk",
        insite_host="portal.example.invalid",
        profile_dir="/tmp/profile",
        op_item="item",
        op_vault="vault",
        dry_run=True,
    )


def args(**kw):
    return argparse.Namespace(year=kw.get("year"), period=kw.get("period"))


@pytest.fixture
def no_browser(monkeypatch):
    """Fail loudly if a test reaches the portal; these are selection tests."""

    @contextmanager
    def boom(*a, **kw):
        raise AssertionError("cmd_submit opened a session when it should not have")

    monkeypatch.setattr(cli, "open_session", boom)


def test_year_without_period_is_refused(cfg, monkeypatch, no_browser):
    store = FakeStore([])
    monkeypatch.setattr(cli, "store", store)
    assert cli.cmd_submit(cfg, args(year=2026)) == 2


def test_period_without_year_is_refused(cfg, monkeypatch, no_browser):
    store = FakeStore([])
    monkeypatch.setattr(cli, "store", store)
    assert cli.cmd_submit(cfg, args(period=8)) == 2


def test_nothing_approved_is_a_clean_no_op(cfg, monkeypatch, no_browser):
    store = FakeStore([])
    monkeypatch.setattr(cli, "store", store)
    assert cli.cmd_submit(cfg, args()) == 0
    assert store.states == []


def test_bare_run_files_the_approved_period_not_the_current_one(cfg, monkeypatch):
    """The regression this exists for: August approved, submit run in September."""
    store = FakeStore([submission(2026, 8, ClaimType.COMMUTE)])
    monkeypatch.setattr(cli, "store", store)

    opened = []

    @contextmanager
    def fake_session(host, **kw):
        opened.append(host)
        yield (object(), object())

    monkeypatch.setattr(cli, "open_session", fake_session)
    monkeypatch.setattr(cli, "open_task_for", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "submit_declaration", lambda page, dry_run: not dry_run)

    assert cli.cmd_submit(cfg, args()) == 0
    assert store.claimable_calls == [(2026, 8, ClaimType.COMMUTE)]
    # in_flight then back to approved, because dry_run leaves it unfiled.
    assert [(y, p, s) for y, p, _c, s, _e in store.states] == [
        (2026, 8, "in_flight"),
        (2026, 8, "approved"),
    ]


def test_each_approved_row_keeps_its_own_period(cfg, monkeypatch):
    store = FakeStore(
        [
            submission(2026, 7, ClaimType.HOME),
            submission(2026, 8, ClaimType.COMMUTE),
        ]
    )
    monkeypatch.setattr(cli, "store", store)

    @contextmanager
    def fake_session(host, **kw):
        yield (object(), object())

    monkeypatch.setattr(cli, "open_session", fake_session)
    monkeypatch.setattr(cli, "open_task_for", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "submit_declaration", lambda page, dry_run: not dry_run)

    assert cli.cmd_submit(cfg, args()) == 0
    assert store.claimable_calls == [
        (2026, 7, ClaimType.HOME),
        (2026, 8, ClaimType.COMMUTE),
    ]


def test_a_digest_that_moved_since_approval_is_refused(cfg, monkeypatch, no_browser):
    store = FakeStore([submission(2026, 8, ClaimType.COMMUTE, digest="approved-digest")])
    store._digest = "something-else"
    monkeypatch.setattr(cli, "store", store)

    assert cli.cmd_submit(cfg, args()) == 0
    assert store.states == [
        (2026, 8, ClaimType.COMMUTE, "awaiting_approval", "digest changed after approval")
    ]


def test_an_explicit_period_only_files_that_period(cfg, monkeypatch):
    store = FakeStore(
        [
            submission(2026, 7, ClaimType.HOME),
            submission(2026, 8, ClaimType.COMMUTE),
        ]
    )
    monkeypatch.setattr(cli, "store", store)

    @contextmanager
    def fake_session(host, **kw):
        yield (object(), object())

    monkeypatch.setattr(cli, "open_session", fake_session)
    monkeypatch.setattr(cli, "open_task_for", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "submit_declaration", lambda page, dry_run: not dry_run)

    assert cli.cmd_submit(cfg, args(year=2026, period=7)) == 0
    assert store.claimable_calls == [(2026, 7, ClaimType.HOME)]
