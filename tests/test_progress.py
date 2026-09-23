"""The picture on every message: measured, honest, and it never stalls."""

from __future__ import annotations

import json
from pathlib import Path

from ecosystem.config import Settings
from ecosystem.notify import SessionNotifier
from ecosystem.progress import Progress, bar, measure, plan_units, settled_calls
from ecosystem.state import AgentRun, MatterState, new_state


def _state(**over: object) -> MatterState:
    state = new_state("demo", "A valve", ["US"])
    for key, value in over.items():
        setattr(state, key, value)
    return state


def _run(prompt: str, phase: str = "P1", status: str = "ok", ms: int = 120_000) -> AgentRun:
    return AgentRun(
        call_id=f"demo-{phase.lower()}-{prompt}-v1-a0",
        phase=phase,
        prompt=prompt,
        agent_id="ip-disclosure",
        session_key=f"agent:ip-disclosure:demo-{prompt}",
        idempotency_key="k",
        status=status,
        duration_ms=ms,
        started_at="2026-09-23T08:00:00+00:00",
    )


def test_the_bar_reads_as_a_bar() -> None:
    assert bar(0.0).startswith("░░░") and bar(0.0).endswith("0%")
    assert bar(1.0).startswith("███") and bar(1.0).endswith("100%")
    assert "42%" in bar(0.42)
    assert len(bar(0.5).split("  ")[0]) == 18


def test_a_failed_step_advances_the_bar() -> None:
    """The run has moved past it; a bar that stalls on failure misreports."""
    state = _state()
    state.agent_runs = [_run("01"), _run("02", status="failed"), _run("03", status="skipped")]
    assert settled_calls(state) == 3
    assert measure(state).done == 3


def test_a_step_still_running_does_not_count() -> None:
    state = _state()
    state.agent_runs = [_run("01"), _run("02", status="started")]
    assert settled_calls(state) == 1


def test_the_plan_lists_items_with_their_agent_and_what_they_wait_for(
    settings: Settings,
) -> None:
    from ecosystem.playbook import load_playbook

    playbook = load_playbook(settings.prompts_path)
    units = plan_units(_state(), playbook, settings)
    p2 = [unit for unit in units if unit.phase == "P2"]
    assert [unit.prompt for unit in p2] == ["06", "07", "08", "09", "10"]
    assert all(unit.agent == "ip-prior-art" for unit in p2)
    line = next(unit.line() for unit in p2 if unit.prompt == "07")
    assert "ip-prior-art" in line and "waits for" in line
    # Both reviewers are planned, and the package steps are counted.
    assert sum(1 for unit in units if unit.phase == "REVIEW") > len(p2)
    assert any(unit.prompt == "39a" for unit in units)


def test_an_estimate_says_how_much_it_is_worth(tmp_path: Path) -> None:
    rough = Progress(done=1, total=10, low_s=600, high_s=2400)
    assert "rough, not yet measured" in rough.estimate()
    assert measure(_state()).measured is False  # a first run has nothing to go on

    # Once earlier matters on this machine have run these steps, the estimate
    # is measured and the range narrows to say so.
    history = tmp_path / "older"
    history.mkdir()
    runs = [
        {"prompt": prompt, "status": "ok", "duration_ms": 60_000}
        for prompt in ("01", "02", "03", "04", "05", "06", "07", "16", "22", "28", "32")
    ]
    (history / "state.json").write_text(json.dumps({"agent_runs": runs}))

    measured = measure(_state(), dossiers_dir=tmp_path)
    assert measured.samples >= 6 and measured.measured is True
    assert "approximate" in measured.estimate()
    assert (measured.high_s - measured.low_s) / max(measured.high_s, 1) < 0.5


def test_parallel_work_is_not_charged_once_per_worker() -> None:
    estimate = measure(_state())
    # Steps in a phase run together, so a phase costs about its slowest member.
    # Charging each one separately would put this near 57 x 300s.
    assert estimate.total > 40
    assert estimate.high_s < 0.35 * estimate.total * 300


def test_history_from_other_matters_is_used_before_a_default(tmp_path: Path) -> None:
    other = tmp_path / "older"
    other.mkdir()
    (other / "state.json").write_text(
        '{"agent_runs": [{"prompt": "01", "status": "ok", "duration_ms": 30000}]}'
    )
    measured = measure(_state(), dossiers_dir=tmp_path)
    assert measured.samples >= 1


def test_every_message_carries_the_bar_and_the_numbers(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    notifier = SessionNotifier(
        "agent:patentcounsel:main",
        "/fake/openclaw",
        runner=calls.append,
        progress_path=tmp_path / "progress.md",
        matter="demo",
        autostart=False,
        interval_s=3600,
        progress=lambda: Progress(
            done=4, total=9, elapsed_s=1268, low_s=720, high_s=960, measured=True
        ),
    )
    notifier.on_event(
        {"event": "call_finished", "matter": "demo", "phase": "P2", "prompt": "07", "detail": {}}
    )
    notifier.close()

    message = calls[0][calls[0].index("--message") + 1]
    assert "07 · done" in message
    assert "44%" in message and "████" in message
    assert "4/9 steps" in message
    assert "elapsed 21m 08s" in message
    assert "est. about 12-16 minutes remaining (approximate)" in message


def test_a_phase_start_shows_the_plan_not_a_count(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    notifier = SessionNotifier(
        "agent:patentcounsel:main",
        "/fake/openclaw",
        runner=calls.append,
        progress_path=tmp_path / "progress.md",
        matter="demo",
        autostart=False,
        interval_s=3600,
        plan=lambda record: [
            "   · 06 Search decomposition — ip-prior-art",
            "   · 07 Chart — ip-prior-art (waits for 06)",
        ],
    )
    notifier.on_event(
        {
            "event": "phase_started",
            "matter": "demo",
            "phase": "P2",
            "detail": {"prompts": ["06", "07"]},
        }
    )
    notifier.close()
    message = calls[0][calls[0].index("--message") + 1]
    assert "06 Search decomposition" in message and "waits for 06" in message


def test_a_long_silence_is_broken_by_naming_the_step_in_flight(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    notifier = SessionNotifier(
        "agent:patentcounsel:main",
        "/fake/openclaw",
        runner=calls.append,
        progress_path=tmp_path / "progress.md",
        matter="demo",
        autostart=False,
        interval_s=3600,
    )
    notifier.on_event(
        {
            "event": "call_started",
            "matter": "demo",
            "phase": "P4",
            "prompt": "x-p4-compose-description",
            "detail": {"agent_id": "ip-spec"},
        }
    )
    notifier._pending.clear()
    notifier._heartbeat()

    message = calls[-1][calls[-1].index("--message") + 1]
    assert "x-p4-compose-description · still running" in message
    assert "so far" in message


def test_the_bar_measures_the_state_on_disk_not_a_stale_copy(tmp_path: Path) -> None:
    """The runner reloads state under its lock; a captured copy never advances."""
    calls: list[list[str]] = []
    state = _state()
    written = tmp_path / "state.json"

    def live() -> MatterState:
        return MatterState.model_validate_json(written.read_text())

    notifier = SessionNotifier(
        "agent:patentcounsel:main",
        "/fake/openclaw",
        runner=calls.append,
        progress_path=tmp_path / "progress.md",
        matter="demo",
        autostart=False,
        interval_s=3600,
        progress=lambda: measure(live()),
    )

    written.write_text(state.model_dump_json())
    notifier.on_event({"event": "call_started", "matter": "demo", "phase": "P1", "prompt": "01"})
    notifier.flush(force=True)
    assert "0/" in calls[-1][calls[-1].index("--message") + 1]

    state.agent_runs = [_run("01"), _run("02"), _run("03")]
    written.write_text(state.model_dump_json())
    notifier.on_event({"event": "call_finished", "matter": "demo", "phase": "P1", "prompt": "03"})
    notifier.flush(force=True)
    message = calls[-1][calls[-1].index("--message") + 1]
    assert "3/" in message and "0/" not in message
