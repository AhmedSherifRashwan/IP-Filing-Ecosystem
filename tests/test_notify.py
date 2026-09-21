"""The ticker: short lines, written instantly, relayed in batches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecosystem.log import EventLog
from ecosystem.notify import MARKER, SessionNotifier, ticker_line


def _rec(event: str, **detail: object) -> dict[str, object]:
    top = {k: detail.pop(k) for k in ("phase", "prompt", "call_id") if k in detail}
    return {"ts": "t", "event": event, "matter": "demo", **top, "detail": detail}


def _notifier(tmp_path: Path, calls: list[list[str]], **kw: object) -> SessionNotifier:
    return SessionNotifier(
        "agent:patentcounsel:dashboard:abc",
        "/fake/openclaw",
        runner=calls.append,
        progress_path=tmp_path / "progress.md",
        matter="demo",
        autostart=False,
        **kw,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# lines
# ---------------------------------------------------------------------------


def test_a_step_reads_like_a_log_not_an_essay() -> None:
    titles = {"07": "Prior-art claim chart"}
    started = ticker_line(
        _rec("call_started", phase="P2", prompt="07", agent_id="ip-prior-art", prompt_chars=41233),
        titles,
    )
    done = ticker_line(
        _rec("call_finished", phase="P2", prompt="07", duration_ms=252_000, attempts=1), titles
    )
    assert started == "P2 prior art · 07 Prior-art claim chart · started (ip-prior-art, 41k chars)"
    assert done == "P2 prior art · 07 Prior-art claim chart · done 4m12s"
    assert len(done) < 90


def test_every_stage_of_a_struggling_step_is_visible() -> None:
    defective = _rec("reply_defective", phase="P4", prompt="19", reason="empty", retry=1)
    assert ticker_line(defective) == "P4 spec · 19 · reply empty, retrying (1)"
    corrected = _rec("contract_corrected", phase="P4", prompt="19", error="assumptions normalised")
    assert "reply repaired" in str(ticker_line(corrected))
    assert ticker_line(_rec("revision_skipped", phase="P4", prompt="x-p4-revise")) == (
        "P4 spec · x-p4-revise · skipped, previous text stands"
    )
    assert ticker_line(_rec("output_partial", phase="REVIEW", prompt="31")) == (
        "REVIEW · 31 · kept as partial output"
    )
    paused = ticker_line(_rec("quota_wait", wait_s=1200, error="session limit"))
    assert str(paused).startswith("paused · provider limit, waiting ~20m")


def test_a_gate_line_carries_the_brief_and_how_to_answer() -> None:
    line = ticker_line(
        _rec("gate_requested", gate="1", options=["accept", "reject"], blocks=["P2"])
    )
    assert line is not None
    assert line.startswith("GATE 1 · decision needed")
    assert "dossiers/demo/briefs/gate-1.md" in line
    assert "how_to_answer" in line


def test_a_cap_leads_with_review_required() -> None:
    line = ticker_line(
        _rec("gate_requested", gate="loopback-cap-novelty", options=["accept-residual"], blocks=[])
    )
    assert line is not None and line.startswith("REVIEW REQUIRED")
    assert "briefs/loopback-cap-novelty.md" in line


def test_milestone_verbosity_keeps_boundaries_and_drops_per_step_chatter() -> None:
    step = _rec("call_finished", phase="P2", prompt="07", duration_ms=1000)
    assert ticker_line(step, verbosity="steps") is not None
    assert ticker_line(step, verbosity="milestones") is None
    done = _rec("phase_completed", phase="P2", version=1, status="complete", trigger="initial")
    assert ticker_line(done, verbosity="milestones") == "P2 prior art · complete · v1"


def test_events_with_nothing_to_say_produce_no_line() -> None:
    assert ticker_line(_rec("output_written", phase="P1", prompt="01")) is None
    assert ticker_line(_rec("run_stopped", reason="gate", status="awaiting_gate")) is None
    assert ticker_line(_rec("gate_requested", gate="1", brief_error="boom")) is None


# ---------------------------------------------------------------------------
# batching
# ---------------------------------------------------------------------------


def test_lines_are_batched_into_one_relay(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    notifier = _notifier(tmp_path, calls, interval_s=3600)
    for prompt in ("06", "07", "08"):
        notifier.on_event(_rec("call_started", phase="P2", prompt=prompt, agent_id="ip-prior-art"))
    assert calls == []  # nothing sent yet: the interval has not elapsed
    notifier.close()

    assert len(calls) == 1
    message = calls[0][calls[0].index("--message") + 1]
    assert message.startswith(MARKER)
    assert message.count("· started") == 3
    assert "exactly as they are" in message


def test_an_urgent_line_does_not_wait_for_the_interval(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    notifier = _notifier(tmp_path, calls, interval_s=3600)
    notifier.on_event(_rec("call_started", phase="P1", prompt="01", agent_id="ip-disclosure"))
    notifier.on_event(_rec("gate_requested", gate="1", options=["accept"], blocks=["P2"]))
    assert len(calls) == 1
    message = calls[0][calls[0].index("--message") + 1]
    assert "01 · started" in message and "GATE 1" in message  # the batch goes with it


def test_every_line_reaches_the_progress_file_immediately(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    notifier = _notifier(tmp_path, calls, interval_s=3600)
    notifier.on_event(_rec("call_started", phase="P1", prompt="01", agent_id="ip-disclosure"))
    notifier.on_event(_rec("call_finished", phase="P1", prompt="01", duration_ms=5000))
    written = (tmp_path / "progress.md").read_text()
    assert "01 · started" in written and "01 · done 5s" in written
    assert calls == []  # the file costs nothing; the relay still waits


def test_close_is_idempotent_and_flushes_once(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    notifier = _notifier(tmp_path, calls, interval_s=3600)
    notifier.on_event(_rec("call_started", phase="P1", prompt="01", agent_id="ip-disclosure"))
    notifier.close()
    notifier.close()
    assert len(calls) == 1


def test_notifier_rejects_a_bare_session_key() -> None:
    with pytest.raises(ValueError):
        SessionNotifier("dashboard:abc", "/fake/openclaw", autostart=False)


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
