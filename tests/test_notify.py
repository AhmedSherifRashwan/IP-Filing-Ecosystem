"""The runner-side push that keeps the human informed without the agent polling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecosystem.log import EventLog
from ecosystem.notify import MARKER, SessionNotifier, format_event


def _rec(event: str, **detail: object) -> dict[str, object]:
    top = {k: detail.pop(k) for k in ("phase", "prompt", "call_id") if k in detail}
    return {"ts": "t", "event": event, "matter": "demo", **top, "detail": detail}


def test_call_finished_names_phase_agent_prompt_and_duration() -> None:
    text = format_event(
        _rec("call_finished", phase="P2", prompt="07", duration_ms=252_000, attempts=1),
        {"07": "Prior-art claim chart"},
    )
    assert text == (
        "Matter demo: Phase 2 — prior art and differentiation (ip-prior-art) — "
        "prompt 07 (Prior-art claim chart) finished in 4m12s."
    )


def test_gate_requested_tells_the_agent_where_the_brief_is() -> None:
    text = format_event(
        _rec("gate_requested", gate="1", options=["accept", "reject"], blocks=["P2", "P3"])
    )
    assert text is not None
    assert "Gate 1" in text
    assert "dossiers/demo/briefs/gate-1.md" in text
    assert "how_to_answer" in text


def test_cap_gate_leads_with_review_required() -> None:
    text = format_event(
        _rec("gate_requested", gate="loopback-cap-novelty", options=["accept-residual"], blocks=[])
    )
    assert text is not None
    assert text.startswith("Matter demo: REVIEW REQUIRED")
    assert "briefs/loopback-cap-novelty.md" in text


def test_noise_events_are_not_relayed() -> None:
    assert format_event(_rec("call_started", phase="P1", prompt="01")) is None
    assert format_event(_rec("output_written", phase="P1", prompt="01")) is None
    assert format_event(_rec("run_stopped", reason="gate", status="awaiting_gate")) is None
    assert format_event(_rec("gate_requested", gate="1", brief_error="boom")) is None


def test_notifier_relays_through_openclaw_agent_argv(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    notifier = SessionNotifier(
        "agent:patentcounsel:dashboard:abc",
        "/fake/openclaw",
        deliver=True,
        runner=calls.append,
    )
    notifier.on_event(_rec("run_failed", phase="P1", error="GatewayAuthError: expired"))
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:4] == [
        "/fake/openclaw",
        "agent",
        "--session-key",
        "agent:patentcounsel:dashboard:abc",
    ]
    message = argv[argv.index("--message") + 1]
    assert message.startswith(MARKER)
    assert "GatewayAuthError: expired" in message
    assert "--deliver" in argv and "--json" in argv


def test_notifier_rejects_a_bare_session_key() -> None:
    with pytest.raises(ValueError):
        SessionNotifier("dashboard:abc", "/fake/openclaw")


def test_event_log_observers_see_records_and_never_break_the_log(tmp_path: Path) -> None:
    events = EventLog(tmp_path / "log.jsonl", "demo")
    seen: list[str] = []

    def bad(_record: dict[str, object]) -> None:
        raise RuntimeError("observer exploded")

    events.subscribe(bad)
    events.subscribe(lambda r: seen.append(str(r["event"])))
    events.emit("phase_started", phase="P1", prompts=["01"])
    assert seen == ["phase_started"]
    lines = (tmp_path / "log.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["event"] == "phase_started"


def test_milestone_verbosity_drops_per_prompt_chatter() -> None:
    rec = _rec("call_finished", phase="P2", prompt="07", duration_ms=1000, attempts=1)
    assert format_event(rec, verbosity="steps") is not None
    assert format_event(rec, verbosity="milestones") is None
    done = _rec("phase_completed", phase="P2", version=1, status="complete", trigger="initial")
    assert format_event(done, verbosity="milestones") is not None
