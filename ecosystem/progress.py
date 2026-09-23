"""How far along a run is, and how much longer -- measured, never assumed.

A run of thirty minutes that says nothing is indistinguishable from a run that
has crashed, so every message the runner posts carries the whole picture: a bar,
what is done out of what is planned, how long it has been going, and an honest
range for what is left.

The estimate is read from history rather than hardcoded. This matter's own
completed steps come first, then the same steps in other matters on this
machine, and only then a deliberately generous default. The width of the range
is the signal: wide when it is a guess, narrow once it is measured. A constant
that says "8-10 minutes" on a run that takes an hour destroys trust in every
number beside it.

Two rules follow from what a bar is for. Parallel work costs roughly the
longest step plus a margin, not the sum of the group. And a step that failed
still advances the bar, because the run has moved past it -- a bar that stalls
on failure misreports a run that is still going.
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ecosystem.config import PHASE_AGENT, REVIEW_AGENTS, Settings
from ecosystem.state import MatterState

log = logging.getLogger("ipfe")

__all__ = ["Progress", "Unit", "bar", "measure", "plan_units"]

BAR_WIDTH: Final[int] = 18
BAR_FULL: Final[str] = "█"
BAR_EMPTY: Final[str] = "░"

# Only used when neither this run nor any other on this machine has ever run
# the step. Deliberately generous: an estimate that keeps expiring reads as a
# stall, and being early is forgivable in a way that being late is not.
_DEFAULT_SECONDS: Final[dict[str, int]] = {
    "analysis": 240,
    "drafting": 360,
    "compose": 600,
    "revise": 480,
    "review": 300,
    "prose": 180,
}
_FALLBACK_SECONDS: Final[int] = 300

# A parallel group costs the slowest member plus a margin for the ones that
# start late, not the sum of its members.
_PARALLEL_MARGIN: Final[float] = 1.25

# Statuses that mean the run is no longer waiting on that step.
_SETTLED: Final[frozenset[str]] = frozenset({"ok", "failed", "skipped", "partial"})


@dataclass(frozen=True)
class Unit:
    """One step of work: what runs it, and what it waits for."""

    phase: str
    prompt: str
    agent: str
    waits_on: tuple[str, ...] = ()
    title: str = ""

    @property
    def key(self) -> str:
        return f"{self.phase}:{self.prompt}"

    def line(self) -> str:
        """One line of a plan, showing the dependency rather than hiding it."""
        name = f"{self.prompt} {self.title}".strip()
        waits = f" (waits for {', '.join(self.waits_on)})" if self.waits_on else ""
        return f"{name} — {self.agent}{waits}"


@dataclass
class Progress:
    """The picture that goes on the end of every message."""

    done: int = 0
    total: int = 0
    elapsed_s: float = 0.0
    low_s: float = 0.0
    high_s: float = 0.0
    measured: bool = False
    samples: int = 0
    remaining: list[Unit] = field(default_factory=list)

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.done / self.total))

    def bar(self) -> str:
        return bar(self.fraction)

    def stats(self, noun: str = "steps") -> str:
        parts = [f"{self.done}/{self.total} {noun}", f"elapsed {_duration(self.elapsed_s)}"]
        parts.append(self.estimate())
        return " · ".join(parts)

    def estimate(self) -> str:
        """The remaining range, labelled with how much it is worth."""
        if self.total and self.done >= self.total:
            return "finishing"
        if self.high_s <= 0:
            return "est. unknown"
        low, high = _minutes(self.low_s), _minutes(self.high_s)
        span = (
            f"about {low}-{high} minutes remaining"
            if low != high
            else f"about {low} minutes remaining"
        )
        return f"est. {span} ({'approximate' if self.measured else 'rough, not yet measured'})"

    def render(self, noun: str = "steps") -> str:
        return f"{self.bar()}\n{self.stats(noun)}"


def bar(fraction: float) -> str:
    filled = int(round(max(0.0, min(1.0, fraction)) * BAR_WIDTH))
    return f"{BAR_FULL * filled}{BAR_EMPTY * (BAR_WIDTH - filled)}  {int(round(fraction * 100))}%"


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------


def _steps() -> dict[str, tuple[str, ...]]:
    """The runner's step lists, imported late to keep the import graph one-way."""
    from ecosystem import runner

    return {
        "P1": runner.P1_PROMPTS,
        "P2": runner.P2_PROMPTS,
        "P3": runner.P3_PROMPTS,
        "P4_ROUND1": runner.P4_ROUND1,
        "P5_ROUND1": runner.P5_ROUND1,
        "P4_REVISE": runner.P4_REVISE,
        "P5_REVISE": runner.P5_REVISE,
        "REVIEW": runner.REVIEW_PROMPTS,
        "CLEAN": runner.CLEAN_PROMPTS,
    }


def _waits(phase: str, prompt: str, playbook: Any) -> tuple[str, ...]:
    """Which sibling steps this one reads, from its own declared inputs."""
    if playbook is None:
        return ()
    try:
        spec = playbook.get(prompt)
    except Exception:  # an unknown id is not worth failing a progress line for
        return ()
    prefix = f"out:{phase}/"
    return tuple(
        item.split("/", 1)[1]
        for item in [*spec.inputs, *spec.optional_inputs]
        if item.startswith(prefix)
    )


def _title(prompt: str, playbook: Any) -> str:
    if playbook is None:
        return ""
    try:
        return str(playbook.get(prompt).title)
    except Exception:
        return ""


def plan_units(
    state: MatterState, playbook: Any = None, settings: Settings | None = None
) -> list[Unit]:
    """Every step this run still intends to take, plus the ones it has taken.

    The pipeline is not a fixed list: the inner loop runs up to its cap and the
    review runs up to its budget. The plan therefore counts what the run is
    currently committed to, which grows only when a loop-back is actually
    routed, so the bar does not jump backwards.
    """
    settings = settings or Settings()
    steps = _steps()
    units: list[Unit] = []

    def add(phase: str, prompts: tuple[str, ...], agent: str | None = None) -> None:
        for prompt in prompts:
            units.append(
                Unit(
                    phase=phase,
                    prompt=prompt,
                    agent=agent or PHASE_AGENT.get(phase, "?"),
                    waits_on=_waits(phase, prompt, playbook),
                    title=_title(prompt, playbook),
                )
            )

    add("P1", steps["P1"])
    add("P2", steps["P2"])
    add("P3", steps["P3"])

    rounds = max(1, min(int(state.inner_loop.round or 1), int(settings.inner_loop_cap)))
    for index in range(rounds):
        add("P4", steps["P4_ROUND1"] if index == 0 else steps["P4_REVISE"])
        add("P5", steps["P5_ROUND1"] if index == 0 else steps["P5_REVISE"])

    reviewers = list(REVIEW_AGENTS) if settings.second_reviewer else [REVIEW_AGENTS[0]]
    review_rounds = max(1, len(state.review.versions) or 1)
    for _ in range(review_rounds):
        for reviewer in reviewers:
            add("REVIEW", steps["REVIEW"], agent=reviewer)

    add("PACKAGE", ("39a",), agent=PHASE_AGENT.get("P4", "ip-spec"))
    add("REVIEW", steps["CLEAN"], agent=REVIEW_AGENTS[0])
    return units


def settled_calls(state: MatterState) -> int:
    """Steps the run is no longer waiting on: finished, failed or skipped alike."""
    seen: set[str] = set()
    for run in state.agent_runs:
        if run.status in _SETTLED:
            # Correction turns share a step; the step settles once, not twice.
            seen.add(f"{run.phase}:{run.prompt}:{run.call_id.split('-a')[0]}")
    return len(seen)


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------


def _durations_from(state: MatterState) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for run in state.agent_runs:
        if run.status == "ok" and run.duration_ms and run.prompt:
            out.setdefault(str(run.prompt), []).append(float(run.duration_ms) / 1000.0)
    return out


def _durations_from_history(dossiers_dir: Path, skip: str) -> dict[str, list[float]]:
    """The same steps in other matters on this machine."""
    out: dict[str, list[float]] = {}
    if not dossiers_dir.is_dir():
        return out
    for path in sorted(dossiers_dir.glob("*/state.json")):
        if path.parent.name == skip:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for run in data.get("agent_runs") or []:
            if run.get("status") == "ok" and run.get("duration_ms"):
                out.setdefault(str(run.get("prompt")), []).append(
                    float(run["duration_ms"]) / 1000.0
                )
    return out


def _kind(prompt: str, playbook: Any) -> str:
    if playbook is None:
        return "analysis"
    try:
        return str(playbook.get(prompt).kind)
    except Exception:
        return "analysis"


def _estimate_one(
    prompt: str,
    mine: dict[str, list[float]],
    theirs: dict[str, list[float]],
    playbook: Any,
) -> tuple[float, int]:
    """Seconds for one step, and how many samples that rests on."""
    for source in (mine, theirs):
        samples = source.get(prompt)
        if samples:
            return statistics.median(samples), len(samples)
    return float(_DEFAULT_SECONDS.get(_kind(prompt, playbook), _FALLBACK_SECONDS)), 0


def _since(start: datetime) -> float:
    moment = start if start.tzinfo else start.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - moment).total_seconds())


def _elapsed(state: MatterState) -> float:
    starts = [run.started_at for run in state.agent_runs if run.started_at]
    if not starts:
        return 0.0
    try:
        first = datetime.fromisoformat(min(starts))
    except ValueError:
        return 0.0
    if first.tzinfo is None:
        first = first.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - first).total_seconds())


def measure(
    state: MatterState,
    playbook: Any = None,
    settings: Settings | None = None,
    dossiers_dir: Path | None = None,
    since: datetime | None = None,
) -> Progress:
    """Where the run is, and how long the rest should take.

    `since` is when *this* run started. A matter resumed after two days has not
    been working for two days, and saying so would make every number beside it
    look invented.
    """
    settings = settings or Settings()
    units = plan_units(state, playbook, settings)
    done = min(settled_calls(state), len(units))

    finished = {f"{run.phase}:{run.prompt}" for run in state.agent_runs if run.status in _SETTLED}
    remaining = [unit for unit in units if unit.key not in finished]

    mine = _durations_from(state)
    theirs = (
        _durations_from_history(dossiers_dir, state.matter.slug) if dossiers_dir is not None else {}
    )

    # Steps in one phase largely run in parallel, so a phase costs its slowest
    # member plus a margin. Summing them would be a wild over-estimate.
    by_phase: dict[str, list[float]] = {}
    samples = 0
    for unit in remaining:
        seconds, count = _estimate_one(unit.prompt, mine, theirs, playbook)
        samples += count
        by_phase.setdefault(f"{unit.phase}:{unit.agent}", []).append(seconds)
    total = sum(max(group) * _PARALLEL_MARGIN for group in by_phase.values())

    measured = samples >= 3
    spread = 0.2 if samples >= 6 else 0.4 if measured else 0.6
    return Progress(
        done=done,
        total=len(units),
        elapsed_s=_since(since) if since is not None else _elapsed(state),
        low_s=total * (1 - spread),
        high_s=total * (1 + spread),
        measured=measured,
        samples=samples,
        remaining=remaining,
    )


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------


def _duration(seconds: float) -> str:
    seconds = int(max(0.0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def _minutes(seconds: float) -> int:
    return max(1, int(round(seconds / 60.0)))
