"""Push runner progress into the OpenClaw session a human is watching.

The front-door agent cannot poll a run that lasts hours from inside one chat
turn: the turn ends, and anything still waiting is dropped. Nucleus solves this
by having the worker post progress itself, and this module is that worker-side
push.

It is a ticker, not a report. Every step writes one short line -- `started`,
`done 2m41s`, `retry 1 (head-truncated)`, `skipped` -- to `progress.md` the
instant it happens, at no cost. Those lines reach the chat in batches: nothing
can be posted into an OpenClaw session without spending a model turn, so the
runner collects lines and flushes them on an interval, and immediately for
anything the human must act on (a gate, a cap, a failure, a pause). The agent
is told to post the lines verbatim, so an update reads like a log, not an essay.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from ecosystem.config import PHASE_AGENT

log = logging.getLogger("ipfe")

__all__ = ["MARKER", "SessionNotifier", "format_event", "ticker_line"]

# Every pushed message starts with this so the agent (and its skill) can tell a
# runner update from a human message and relay it instead of re-verifying it.
MARKER: Final[str] = "[IPFE runner update]"

_RELAY_INSTRUCTION: Final[str] = (
    "Progress lines from the IP Filing Ecosystem runner, newest last. Post them to the "
    "person exactly as they are, as a compact list, and nothing else: no preamble, no "
    "summary, no commentary, no tool calls to re-check them. The one exception is a line "
    "starting GATE, REVIEW REQUIRED or FAILED: read the brief or file it names and put "
    "the decision, or the verbatim error, to the person in the same reply under the list."
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

# Short tag per phase for the ticker; the long title is for phase boundaries.
_PHASE_TAGS: Final[dict[str, str]] = {
    "P1": "P1 capture",
    "P2": "P2 prior art",
    "P3": "P3 embodiments",
    "P4": "P4 spec",
    "P5": "P5 claims",
    "REVIEW": "REVIEW",
    "PACKAGE": "PACKAGE",
}

# Events that produce a ticker line at all. Everything else stays in log.jsonl.
_NOTIFIED: Final[frozenset[str]] = frozenset(
    {
        "run_started",
        "phase_started",
        "phase_completed",
        "call_started",
        "call_finished",
        "call_failed",
        "contract_corrected",
        "contract_failed",
        "reply_defective",
        "revision_skipped",
        "output_partial",
        "terminology_violation",
        "inner_loop_round",
        "gate_requested",
        "gate_decided",
        "loopback_routed",
        "loopback_cap_reached",
        "package_built",
        "search_completed",
        "search_unavailable",
        "quota_wait",
        "quota_resumed",
        "run_failed",
        "run_stopped",
    }
)

# Phase boundaries, decisions, failures and the package: what a milestones-only
# run says.
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
        "revision_skipped",
        "output_partial",
        "quota_wait",
        "search_unavailable",
    }
)

# A human is waiting on these, so they go out without waiting for the interval.
_URGENT: Final[frozenset[str]] = frozenset(
    {
        "gate_requested",
        "loopback_cap_reached",
        "run_failed",
        "run_stopped",
        "quota_wait",
        "package_built",
    }
)


def _tag(phase: str | None) -> str:
    return _PHASE_TAGS.get(phase or "", phase or "run")


def _phase_label(phase: str | None) -> str:
    """Long form, for the places that name a phase and its agent."""
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


def _short(text: Any, limit: int = 140) -> str:
    one_line = " ".join(str(text or "").split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def ticker_line(
    record: dict[str, Any],
    prompt_titles: dict[str, str] | None = None,
    verbosity: str = "steps",
) -> str | None:
    """One short progress line for an event, or None when it is not worth a line."""
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
    where = _tag(phase)
    step = f"{where} · {prompt}" + (f" {title}" if title else "")

    if event == "run_started":
        cursor = (detail.get("cursor") or {}).get("phase") or "the start"
        until = detail.get("until")
        return f"run started from {cursor}" + (f", stopping at {until}" if until else "")
    if event == "phase_started":
        count = len(detail.get("prompts") or [])
        return f"{where} · started, {count} step(s)"
    if event == "phase_completed":
        trigger = detail.get("trigger", "")
        why = f" ({trigger})" if trigger and trigger != "initial" else ""
        return f"{where} · {detail.get('status', 'complete')} · v{detail.get('version')}{why}"
    if event == "call_started":
        agent = detail.get("agent_id", "")
        chars = detail.get("prompt_chars")
        size = f", {int(chars) // 1000}k chars" if isinstance(chars, int) else ""
        return f"{step} · started ({agent}{size})"
    if event == "call_finished":
        took = _duration(detail.get("duration_ms"))
        attempts = detail.get("attempts")
        extra = f", {attempts} attempts" if attempts and int(attempts) > 1 else ""
        return f"{step} · done{f' {took}' if took else ''}{extra}"
    if event == "reply_defective":
        return f"{step} · reply {detail.get('reason')}, retrying ({detail.get('retry')})"
    if event == "contract_corrected":
        return f"{step} · reply repaired · {_short(detail.get('error'), 90)}"
    if event == "contract_failed":
        if detail.get("critical"):
            return f"FAILED · {step} · no usable reply after retries · run stops"
        return f"{step} · no usable reply after retries"
    if event == "revision_skipped":
        return f"{step} · skipped, previous text stands"
    if event == "output_partial":
        return f"{step} · kept as partial output"
    if event == "terminology_violation":
        terms = detail.get("terms") or [
            v.get("term") for v in (detail.get("violations") or []) if isinstance(v, dict)
        ]
        return f"{step} · terminology drift: {_short(', '.join(str(t) for t in terms), 80)}"
    if event == "call_failed":
        return f"{step} · call failed · {_short(detail.get('error'), 110)}"
    if event == "inner_loop_round":
        if detail.get("converged") is None:
            return f"P4/P5 inner loop · round {detail.get('round')} started"
        state = "converged" if detail.get("converged") else "open"
        return (
            f"P4/P5 inner loop · round {detail.get('round')} {state} "
            f"({detail.get('disclosure_requests')} disclosure requests)"
        )
    if event == "search_completed":
        return (
            f"{where} · prior-art search · {detail.get('queries')} queries, "
            f"{detail.get('written')} candidates"
        )
    if event == "search_unavailable":
        return f"{where} · prior-art search unavailable · {_short(detail.get('error'), 90)}"
    if event == "loopback_routed":
        categories = ", ".join(detail.get("categories") or [])
        if not categories:
            return "REVIEW · review clean, no loop-back"
        return (
            f"REVIEW · {len(detail.get('findings') or [])} critical/high in [{categories}] · "
            f"loop-back cycle {detail.get('cycle')} → {', '.join(detail.get('route') or [])}"
        )
    if event == "loopback_cap_reached":
        return f"REVIEW REQUIRED · loop-back cap on {detail.get('category')} · run blocked"
    if event == "quota_wait":
        minutes = max(1, int(detail.get("wait_s") or 0) // 60)
        return f"paused · provider limit, waiting ~{minutes}m, will resume by itself"
    if event == "quota_resumed":
        return "resumed · provider limit cleared"
    if event == "package_built":
        if detail.get("kind") == "final":
            return f"PACKAGE · final package built · {detail.get('path')}"
        if verbosity == "milestones":
            return None
        return "REVIEW · package assembled (description, figures, claims)"
    if event == "gate_requested":
        gate = detail.get("gate")
        if detail.get("brief_error"):
            return None
        cap = str(gate).startswith("loopback-cap")
        stem = str(gate) if cap else f"gate-{gate}"
        options = ", ".join(detail.get("options") or [])
        head = "REVIEW REQUIRED" if cap else f"GATE {gate}"
        return (
            f"{head} · decision needed · options: {options} · "
            f"brief: dossiers/{matter}/briefs/{stem}.md · "
            f"record it with the how_to_answer line from `ipfe status --matter {matter}`"
        )
    if event == "gate_decided":
        return f"gate {detail.get('gate')} · {detail.get('decision')} by {detail.get('by')}"
    if event == "run_failed":
        return f"FAILED · {_tag(phase)} · {_short(detail.get('error'), 160)}"
    if event == "run_stopped":
        reason = detail.get("reason")
        if detail.get("status") == "complete":
            return "COMPLETE · Gate 3 accepted, the package is final"
        if reason in {"gate", "cap"}:
            return None  # the gate line already said it
        if reason == "until":
            return "run stopped at the requested point (--until)"
        if reason == "done":
            return "run finished its work for now"
        return None
    return None


def format_event(
    record: dict[str, Any],
    prompt_titles: dict[str, str] | None = None,
    verbosity: str = "steps",
) -> str | None:
    """Backwards-compatible alias for `ticker_line`."""
    return ticker_line(record, prompt_titles, verbosity)


class SessionNotifier:
    """Collect ticker lines and relay them into one OpenClaw session in batches."""

    def __init__(
        self,
        session_key: str,
        openclaw_bin: str | Path,
        *,
        deliver: bool = False,
        log_path: Path | None = None,
        progress_path: Path | None = None,
        prompt_titles: dict[str, str] | None = None,
        timeout_s: int = 180,
        interval_s: int = 60,
        runner: Any = None,
        verbosity: str = "steps",
        matter: str = "",
        autostart: bool = True,
    ) -> None:
        if not session_key.startswith("agent:"):
            raise ValueError("notify session key must look like agent:<id>:<session>")
        self.session_key = session_key
        self.openclaw_bin = str(Path(openclaw_bin).expanduser())
        self.deliver = deliver
        self.log_path = log_path
        self.progress_path = progress_path
        self.prompt_titles = prompt_titles or {}
        self.timeout_s = timeout_s
        self.interval_s = max(10, int(interval_s))
        self.verbosity = verbosity
        self.matter = matter
        self._runner = runner or self._spawn
        self._lock = threading.RLock()
        self._pending: list[str] = []
        self._last_flush = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sent: list[str] = []
        if autostart:
            self.start()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Flush in the background so a slow relay never delays an agent call."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="ipfe-notify", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Send whatever is pending and stop the flusher. Safe to call twice."""
        self._stop.set()
        self.flush(force=True)
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.wait(2.0):
            if self._pending and (time.monotonic() - self._last_flush) >= self.interval_s:
                self.flush()

    # -- observer -----------------------------------------------------------

    def on_event(self, record: dict[str, Any]) -> None:
        # The file gets every step: it costs nothing and it is what someone
        # tails when they want to watch. `verbosity` governs only what is worth
        # spending a model turn to relay into the chat.
        full = ticker_line(record, self.prompt_titles, "steps")
        if full is not None:
            self._write_progress(full)
        line = (
            full
            if self.verbosity == "steps"
            else ticker_line(record, self.prompt_titles, self.verbosity)
        )
        if line is None:
            return
        with self._lock:
            self._pending.append(line)
        if record.get("event") in _URGENT:
            self.flush(force=True)

    def send(self, text: str) -> None:
        """Queue one line as if it were an event, and send at once."""
        self._write_progress(text)
        with self._lock:
            self._pending.append(text)
        self.flush(force=True)

    # -- delivery -----------------------------------------------------------

    def flush(self, force: bool = False) -> None:
        with self._lock:
            if not self._pending:
                return
            if not force and (time.monotonic() - self._last_flush) < self.interval_s:
                return
            lines, self._pending = self._pending, []
            self._last_flush = time.monotonic()
            self.sent.extend(lines)
            header = f"{MARKER} {self.matter or 'matter'} · {datetime.now().strftime('%H:%M')}"
            message = "\n".join([header, *lines, "", f"({_RELAY_INSTRUCTION})"])
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
            self._runner(argv)

    def _write_progress(self, line: str) -> None:
        """Append to `progress.md` at once: the zero-latency record of the run."""
        if self.progress_path is None:
            return
        try:
            self.progress_path.parent.mkdir(parents=True, exist_ok=True)
            with self.progress_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
        except OSError as exc:  # pragma: no cover - disk-full class of failure
            log.warning("progress file write failed: %s", exc)

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
