"""The liveness heartbeat.

slackd has no HTTP port, so its probe reads the age of a file. The file has to
be rewritten on a timer: the handlers rewrite it too, but a week with no
approvals delivers no interactions, and a probe waiting on one kills a process
that is connected and working. That is not hypothetical -- it restarted the
pod every five minutes until this loop existed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from afas_declaraties import slackd


class FakeClient:
    def __init__(self, connected):
        self._connected = list(connected)
        self.checks = 0

    def is_connected(self):
        self.checks += 1
        return self._connected.pop(0) if self._connected else False


class FakeHandler:
    def __init__(self, client):
        self.client = client
        self.connected = False
        self.started = False

    def connect(self):
        self.connected = True

    def start(self):  # pragma: no cover - the bug was calling this
        self.started = True


class Stop(Exception):
    pass


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Run `run()` for a fixed number of loop passes, then stop it.

    Counts heartbeats rather than reading the file, so "did not refresh" is
    distinguishable from "refreshed with the same value".
    """
    beat = tmp_path / "healthy"
    monkeypatch.setattr(slackd, "HEARTBEAT", beat)
    monkeypatch.setattr(slackd, "build_app", lambda cfg: object())

    def build(connected, passes):
        handler = FakeHandler(FakeClient(connected))
        monkeypatch.setattr(slackd, "SocketModeHandler", lambda app, token: handler)

        beats = []
        real_touch = slackd._touch_heartbeat

        def counting_touch():
            beats.append(True)
            real_touch()

        monkeypatch.setattr(slackd, "_touch_heartbeat", counting_touch)

        calls = {"n": 0}

        def fake_sleep(seconds):
            calls["n"] += 1
            if calls["n"] > passes:
                raise Stop
            return None

        monkeypatch.setattr(slackd.time, "sleep", fake_sleep)
        cfg = SimpleNamespace(slack_app_token="xapp-x", dry_run=True, approver_ids=["U1"])
        return SimpleNamespace(handler=handler, beat=beat, cfg=cfg, beats=beats)

    return build


def run_until_stopped(cfg):
    with pytest.raises(Stop):
        slackd.run(cfg)


def test_the_socket_is_connected_not_blocked_on(harness):
    h = harness([True, True], passes=2)
    run_until_stopped(h.cfg)
    assert h.handler.connected, "run() must call connect(); start() blocks and never beats"
    assert not h.handler.started


def test_the_heartbeat_is_written_before_the_first_sleep(harness):
    h = harness([], passes=0)
    run_until_stopped(h.cfg)
    assert h.beat.exists(), "a pod that connects must pass its probe without waiting a period"
    assert len(h.beats) == 1


def test_a_live_socket_refreshes_on_every_pass(harness):
    h = harness([True, True, True], passes=3)
    run_until_stopped(h.cfg)
    assert h.handler.client.checks == 3
    # One before the loop, then one per pass.
    assert len(h.beats) == 4


def test_a_dead_socket_stops_the_heartbeat(harness):
    """The whole point of the probe: a wedged socket goes stale, it is not papered over."""
    h = harness([False, False], passes=2)
    run_until_stopped(h.cfg)
    assert h.handler.client.checks == 2
    assert len(h.beats) == 1, "only the pre-loop write; a dead socket must not be refreshed"


def test_a_socket_that_drops_midway_stops_refreshing(harness):
    h = harness([True, False, False], passes=3)
    run_until_stopped(h.cfg)
    assert len(h.beats) == 2, "the pre-loop write and the one live pass, then nothing"


def test_the_interval_leaves_room_for_a_slow_pass(harness):
    """Three intervals must fit inside the probe's max age, or one hiccup restarts the pod."""
    assert slackd.HEARTBEAT_INTERVAL_SECONDS * 3 < 180
