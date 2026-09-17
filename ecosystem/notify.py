"""Push runner progress into the OpenClaw session a human is watching.

The front-door agent cannot poll a run that lasts hours from inside one chat
turn: the turn ends, and anything still waiting is dropped. Nucleus solves this
by having the worker post progress itself. This module is that worker-side
push: every meaningful event becomes one message injected into the human's
PatentCounsel session with `openclaw agent --session-key ... --message ...`,
which the agent relays in plain words. The runner never waits for the relay.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Any, Final

from ecosystem.config import PHASE_AGENT

log = logging.getLogger("ipfe")

__all__ = ["MARKER", "SessionNotifier", "format_event"]

# Every pushed message starts with this so the agent (and its skill) can tell a
# runner update from a human message and relay it instead of re-verifying it.
MARKER: Final[str] = "[IPFE runner update]"

_RELAY_INSTRUCTION: Final[str] = (
    "This is an automated progress update from the IP Filing Ecosystem runner about a "
    "matter you launched. It is the ground truth as of this moment: relay it to the "
    "person in one to three plain sentences, in your own voice, without running tools "
    "to re-check it. If it announces a gate, read the brief it names with the read tool "
    "and put the decision to the person in the same reply. If it reports a failure, "
    "quote the error verbatim."
)

_PHASE_TITLES: Final[dict[str, str]] = {
    "P1": "Phase 1 — invention capture",
    "P2": "Phase 2 — prior art and differentiation",
    "P3": "Phase 3 — embodiments and design-arounds",
    "P4": "Phase 4 — specification and drawings",
    "P5": "Phase 5 — claims",
    "REVIEW": "Independent review",
    "PACKAGE": "Final package",
}

# Events that are worth a message. Everything else stays in log.jsonl.
_NOTIFIED: Final[frozenset[str]] = frozenset(
    {
        "run_started",
        "phase_started",
        "call_finished",
        "call_failed",
        "phase_completed",
        "inner_loop_round",
        "gate_requested",
        "gate_decided",
        "loopback_routed",
        "loopback_cap_reached",
        "package_built",
        "run_failed",
        "run_stopped",
        "terminology_violation",
        "contract_failed",
    }
)


def _consensus(counts: Any) -> str:
    """Trailing clause naming how the two reviewers split, when both of them ran.

    Silent for a single-reviewer run: "0 agreed" would read as disagreement
    rather than as the absence of a second opinion.
    """
    if not isinstance(counts, dict):
        return ""
    agreed = int(counts.get("consensus", 0) or 0)
    a_only = int(counts.get("a_only", 0) or 0)
    b_only = int(counts.get("b_only", 0) or 0)
    if not (agreed or b_only):
        return ""
    return f" (reviewers: {agreed} agreed, {a_only} ip-reviewer only, {b_only} ip-reviewer-b only)"


def _phase_label(phase: str | None) -> str:
    if not phase:
        return ""
    title = _PHASE_TITLES.get(phase, phase)
    agent = PHASE_AGENT.get(phase, "")
    return f"{title} ({agent})" if agent else title


def _duration(ms: Any) -> str:
    try:
        seconds = int(ms) // 1000
    except (TypeError, ValueError):
        return ""
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"


_MILESTONES: Final[frozenset[str]] = frozenset(
    {
        "phase_completed",
        "gate_requested",
        "gate_decided",
        "loopback_routed",
        "loopback_cap_reached",
        "package_built",
        "run_failed",
        "run_stopped",
        "call_failed",
        "contract_failed",
        "terminology_violation",
        "search_completed",
        "search_unavailable",
        "quota_wait",
        "quota_resumed",
    }
)


def format_event(
    record: dict[str, Any],
    prompt_titles: dict[str, str] | None = None,
    verbosity: str = "steps",
) -> str | None:
    """One human line for an event, or None when the event is not worth a message.

    `verbosity="milestones"` keeps only phase boundaries, gates, loop-backs,
    failures and the final package: what an unattended run should say.
    """
    event = record.get("event", "")
    if event not in _NOTIFIED:
        return None
    if verbosity == "milestones" and event not in _MILESTONES:
        return None
    detail: dict[str, Any] = record.get("detail") or {}
    matter = record.get("matter", "")
    phase = record.get("phase")
    prompt = record.get("prompt")
    title = (prompt_titles or {}).get(str(prompt), "")
    prompt_label = f"prompt {prompt} ({title})" if title else f"prompt {prompt}"
    head = f"Matter {matter}"

    if event == "run_started":
        cursor = detail.get("cursor") or {}
        where = cursor.get("phase") or "the start"
        until = detail.get("until")
        stop = f", stopping at {until}" if until else ""
        return f"{head}: run started from {where}{stop}. Updates follow as each step finishes."
    if event == "phase_started":
        prompts = detail.get("prompts") or []
        return f"{head}: {_phase_label(phase)} started — {len(prompts)} prompt(s) to run."
    if event == "call_finished":
        took = _duration(detail.get("duration_ms"))
        took = f" in {took}" if took else ""
        attempts = detail.get("attempts")
        corr = " after one correction turn" if attempts and int(attempts) > 1 else ""
        return f"{head}: {_phase_label(phase)} — {prompt_label} finished{took}{corr}."
    if event == "call_failed":
        return f"{head}: {_phase_label(phase)} — {prompt_label} FAILED: {detail.get('error')}"
    if event == "contract_failed":
        return (
            f"{head}: {_phase_label(phase)} — {prompt_label} returned a reply that did not "
            f"follow the output contract even after a correction turn. The run stops here."
        )
    if event == "terminology_violation":
        return (
            f"{head}: {_phase_label(phase)} — {prompt_label} introduced undeclared terminology "
            f"and was rejected: {detail.get('terms') or detail.get('violations') or ''}"
        )
    if event == "phase_completed":
        status = detail.get("status", "complete")
        version = detail.get("version")
        trigger = detail.get("trigger", "")
        why = f" ({trigger})" if trigger and trigger != "initial" else ""
        return f"{head}: {_phase_label(phase)} {status} — version v{version}{why}."
    if event == "inner_loop_round":
        return (
            f"{head}: specification and claims inner loop, round {detail.get('round')} "
            f"(ip-spec and ip-claims revising against each other)."
        )
    if event == "gate_requested":
        gate = detail.get("gate")
        if detail.get("brief_error"):
            return None  # the follow-up gate_requested with the file is the one to relay
        options = ", ".join(detail.get("options") or [])
        blocks = ", ".join(detail.get("blocks") or [])
        cap = str(gate).startswith("loopback-cap")
        headline = (
            f"{head}: REVIEW REQUIRED — the reviewer's loop-back cap was reached ({gate})."
            if cap
            else f"{head}: stopped at Gate {gate}; a human decision is needed."
        )
        return (
            f"{headline} Options: {options}. Blocks: {blocks or 'nothing'}. "
            f"Brief: dossiers/{matter}/briefs/{'gate-' if not cap else ''}{gate}.md "
            f"(read it and put the decision to the person). "
            f"Record it with: uv run ipfe status --matter {matter} → how_to_answer."
        )
    if event == "gate_decided":
        return (
            f"{head}: gate {detail.get('gate')} recorded as '{detail.get('decision')}' "
            f"by {detail.get('by')}. Relaunch with: uv run ipfe run --matter {matter} --background."
        )
    if event == "loopback_routed":
        cats = ", ".join(detail.get("categories") or [])
        n = len(detail.get("findings") or [])
        return (
            f"{head}: independent review raised {n} critical/high finding(s) in [{cats}]; "
            f"loop-back cycle {detail.get('cycle')} routed to "
            f"{', '.join(detail.get('route') or [])}{_consensus(detail.get('consensus'))}."
        )
    if event == "loopback_cap_reached":
        return (
            f"{head}: REVIEW REQUIRED — loop-back cap reached for "
            f"{detail.get('category')}; the run is blocked pending a human decision."
        )
    if event == "package_built":
        kind = detail.get("kind")
        if verbosity == "milestones" and kind != "final":
            return None
        if kind == "final":
            return f"{head}: final filing package built at {detail.get('path')}."
        return (
            f"{head}: review package assembled (description, figures, claims only) for ip-reviewer."
        )
    if event == "quota_wait":
        mins = max(1, int(detail.get("wait_s") or 0) // 60)
        return (
            f"{head}: paused — the model provider reports a usage limit "
            f"({detail.get('error')}). The run is waiting about {mins} min and will retry "
            f"by itself; nothing is lost."
        )
    if event == "quota_resumed":
        return f"{head}: provider limit cleared; the run resumed from where it paused."
    if event == "search_completed":
        return (
            f"{head}: prior-art search done — {detail.get('queries')} queries sent to Google "
            f"Patents, {detail.get('written')} candidate references retrieved for charting."
        )
    if event == "search_unavailable":
        return (
            f"{head}: prior-art search could not run ({detail.get('error')}); Phase 2 continues "
            f"on the references supplied by hand, and the gap is recorded."
        )
    if event == "run_failed":
        return f"{head}: RUN FAILED in {_phase_label(phase)}: {detail.get('error')}"
    if event == "run_stopped":
        reason = detail.get("reason")
        status = detail.get("status")
        if reason in {"gate", "cap"}:
            return None  # gate_requested already said it
        if status == "complete":
            return f"{head}: complete — Gate 3 accepted; the package is final."
        if reason == "until":
            return f"{head}: run stopped at the requested point (--until)."
        if reason == "done":
            return f"{head}: run finished its work for now."
        return None
    return None


class SessionNotifier:
    """Relay formatted events into one OpenClaw session, without blocking the run."""

    def __init__(
        self,
        session_key: str,
        openclaw_bin: str | Path,
        *,
        deliver: bool = False,
        log_path: Path | None = None,
        prompt_titles: dict[str, str] | None = None,
        timeout_s: int = 180,
        runner: Any = None,
        verbosity: str = "steps",
    ) -> None:
        if not session_key.startswith("agent:"):
            raise ValueError("notify session key must look like agent:<id>:<session>")
        self.session_key = session_key
        self.openclaw_bin = str(Path(openclaw_bin).expanduser())
        self.deliver = deliver
        self.log_path = log_path
        self.prompt_titles = prompt_titles or {}
        self.timeout_s = timeout_s
        self.verbosity = verbosity
        self._runner = runner or self._spawn
        self._lock = threading.Lock()
        self.sent: list[str] = []

    # -- observer -----------------------------------------------------------

    def on_event(self, record: dict[str, Any]) -> None:
        text = format_event(record, self.prompt_titles, self.verbosity)
        if text is None:
            return
        self.send(text)

    def send(self, text: str) -> None:
        message = f"{MARKER} {text}\n\n({_RELAY_INSTRUCTION})"
        argv = [
            self.openclaw_bin,
            "agent",
            "--session-key",
            self.session_key,
            "--message",
            message,
            "--timeout",
            str(self.timeout_s),
            "--json",
        ]
        if self.deliver:
            argv.append("--deliver")
        with self._lock:
            self.sent.append(text)
            self._runner(argv)

    # -- transport ----------------------------------------------------------

    def _spawn(self, argv: list[str]) -> None:
        """Fire and forget. The relay's own output goes to notify.log, never to the run."""
        handle: Any = subprocess.DEVNULL
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.log_path.open("ab")
        try:
            subprocess.Popen(  # noqa: S603 - argv is ours
                argv,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            log.warning("notify relay could not start: %s", exc)
        finally:
            if handle is not subprocess.DEVNULL:
                handle.close()
