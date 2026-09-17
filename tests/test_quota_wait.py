"""A provider session limit pauses a run; it does not end it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ecosystem.calls import Caller, _seconds_until_reset
from ecosystem.transport import GatewayRateLimited, classify_error


def test_session_limit_message_is_a_rate_limit() -> None:
    err = classify_error("You've hit your session limit · resets 3:20pm (Europe/London)")
    assert isinstance(err, GatewayRateLimited)


def test_reset_time_is_parsed_with_a_margin() -> None:
    now = datetime(2026, 9, 17, 15, 0, tzinfo=timezone(timedelta(hours=1)))
    secs = _seconds_until_reset("resets 3:20pm (Europe/London)", now=now)
    assert secs == pytest.approx(20 * 60 + 90)
    assert _seconds_until_reset("no time here") is None


def test_send_waits_then_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    caller = Caller.__new__(Caller)
    import threading

    caller.lock = threading.RLock()
    caller.settings = SimpleNamespace(quota_wait_max_s=3600, quota_poll_s=5)
    events: list[str] = []
    caller.events = SimpleNamespace(emit=lambda e, **k: events.append(e))
    slept: list[float] = []
    caller._sleep = slept.append  # type: ignore[assignment]
    attempts = {"n": 0}

    def inner(*_a: object) -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise GatewayRateLimited("You've hit your session limit")
        return "reply"

    caller._send_inner = inner  # type: ignore[assignment]
    result = SimpleNamespace(phase="P4", prompt_id="19", call_id="c")
    caller.lock.acquire()
    assert caller._send(result, None, "t", 10) == "reply"  # type: ignore[arg-type]
    assert attempts["n"] == 3 and len(slept) == 2
    assert events.count("quota_wait") == 2 and events.count("quota_resumed") == 2
