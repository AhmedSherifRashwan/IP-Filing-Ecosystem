"""`state.json` - the authoritative record of where a matter is.

One process mutates one matter at a time (`Lock`), writes are atomic
(tmp + `os.replace`), and anything derivable from disk can be rebuilt by
`repair_from_disk`. The invariant that matters: a phase's `current_version`
names a directory that exists and whose VERSION.json agrees.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ecosystem.config import FINDING_CATEGORIES, PHASE_AGENT, PHASES

log = logging.getLogger("ipfe")

__all__ = [
    "AgentRun",
    "Cursor",
    "GateState",
    "InnerLoopState",
    "Lock",
    "LockHeld",
    "LoopbackState",
    "MatterInfo",
    "MatterState",
    "PhaseState",
    "ReviewState",
    "StateError",
    "TerminologyState",
    "VersionRecord",
    "load",
    "new_state",
    "repair_from_disk",
    "save",
]

SCHEMA_VERSION = 1

PhaseStatus = Literal["idle", "running", "complete", "failed"]
RunStatus = Literal["idle", "running", "awaiting_gate", "blocked", "failed", "complete"]
GateStatus = Literal["not_requested", "pending", "decided"]
VersionStatus = Literal["running", "complete", "failed"]


class StateError(RuntimeError):
    """State on disk is unreadable or inconsistent with the schema."""


class LockHeld(RuntimeError):
    """Another live process holds this matter's lock."""

    def __init__(self, path: Path, pid: int) -> None:
        super().__init__(f"{path} is held by live pid {pid}")
        self.path = path
        self.pid = pid


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class _Loose(BaseModel):
    """Base for history records: extra keys survive a round trip.

    History entries are written by the runner and the loop-back engine and read
    by humans. Dropping a field they added because this module has not been
    updated yet would lose evidence.
    """

    model_config = ConfigDict(extra="allow")


class NotifyTarget(BaseModel):
    """Where runner progress is pushed: an OpenClaw session the human is watching."""

    model_config = ConfigDict(extra="forbid")

    session_key: str
    deliver: bool = False
    verbosity: Literal["steps", "milestones"] = "steps"


class MatterInfo(BaseModel):
    slug: str
    title: str
    jurisdictions: list[str] = Field(default_factory=list)


class Cursor(BaseModel):
    phase: str | None = None
    step: str | None = None
    inner_round: int = 0
    call_id: str | None = None


class VersionRecord(BaseModel):
    v: int
    dir: str
    trigger: str
    prompts_done: list[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=_now)
    finished_at: str | None = None
    status: VersionStatus = "running"


class PhaseState(BaseModel):
    agent: str
    status: PhaseStatus = "idle"
    current_version: int | None = None
    versions: list[VersionRecord] = Field(default_factory=list)

    def version(self, v: int) -> VersionRecord | None:
        return next((rec for rec in self.versions if rec.v == v), None)

    def latest_complete(self) -> VersionRecord | None:
        done = [rec for rec in self.versions if rec.status == "complete"]
        return max(done, key=lambda rec: rec.v) if done else None


class GateState(BaseModel):
    status: GateStatus = "not_requested"
    requested_at: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None
    decision: str | None = None
    note: str | None = None
    file: str | None = None
    blocks: list[str] = Field(default_factory=list)


class InnerLoopRound(_Loose):
    round: int
    started_at: str | None = None
    finished_at: str | None = None
    disclosure_requests: int = 0
    open_disagreements: int = 0


class InnerLoopState(BaseModel):
    round: int = 0
    cap: int = 3
    converged: bool = False
    rounds: list[InnerLoopRound] = Field(default_factory=list)


class LoopbackEvent(_Loose):
    at: str = Field(default_factory=_now)
    category: str
    cycle: int
    finding_ids: list[str] = Field(default_factory=list)
    route: list[str] = Field(default_factory=list)


class ForcedDecision(_Loose):
    at: str = Field(default_factory=_now)
    category: str
    decision: str
    decided_by: str
    note: str | None = None


class LoopbackState(BaseModel):
    cap_per_category: int = 2
    cycles: dict[str, int] = Field(default_factory=lambda: dict.fromkeys(FINDING_CATEGORIES, 0))
    history: list[LoopbackEvent] = Field(default_factory=list)
    forced_decisions: list[ForcedDecision] = Field(default_factory=list)
    # Formal fixes have their own counter: they never narrow scope, so they must
    # not consume a category's loop-back budget (plan s3.9).
    formal_fix_passes: int = 0


class ReviewVersion(_Loose):
    v: int
    counts: dict[str, int] = Field(default_factory=dict)
    open_categories: list[str] = Field(default_factory=list)
    gate14_passed: bool | None = None
    # Which reviewers actually returned envelopes for this round, and how the
    # merge landed: {"consensus": n, "a_only": n, "b_only": n}. Both default
    # empty, so a state.json written by the single-reviewer pipeline loads.
    reviewers: list[str] = Field(default_factory=list)
    consensus: dict[str, int] = Field(default_factory=dict)


class ReviewState(BaseModel):
    versions: list[ReviewVersion] = Field(default_factory=list)


class TerminologyState(BaseModel):
    locked_at: str | None = None
    locked_from: str | None = None
    sha256: str | None = None
    terms: int = 0
    violations_blocked: int = 0
    drift_logged: int = 0


class AgentRun(_Loose):
    call_id: str
    phase: str | None = None
    prompt: str | None = None
    agent_id: str
    session_key: str | None = None
    idempotency_key: str | None = None
    transport: str | None = None
    run_id: str | None = None
    status: str = "started"
    provider: str | None = None
    model: str | None = None
    duration_ms: int | None = None
    prompt_chars: int | None = None
    reply_chars: int | None = None
    attempt: int = 0
    retry: int = 0
    error: str | None = None
    started_at: str = Field(default_factory=_now)
    finished_at: str | None = None


class LastError(_Loose):
    at: str = Field(default_factory=_now)
    kind: str
    message: str
    phase: str | None = None
    prompt: str | None = None
    call_id: str | None = None


def _default_phases() -> dict[str, PhaseState]:
    return {phase: PhaseState(agent=PHASE_AGENT[phase]) for phase in PHASES}


def _default_gates() -> dict[str, GateState]:
    return {"1": GateState(), "2": GateState(), "3": GateState()}


def _default_counters() -> dict[str, int]:
    return {
        "calls": 0,
        "retries": 0,
        "contract_corrections": 0,
        "terminology_violations": 0,
        "outputs": 0,
    }


class MatterState(BaseModel):
    """Root document. Every field has a default so an older file still loads."""

    schema_version: int = SCHEMA_VERSION
    matter: MatterInfo
    status: RunStatus = "idle"
    cursor: Cursor = Field(default_factory=Cursor)
    phases: dict[str, PhaseState] = Field(default_factory=_default_phases)
    # Keys "1", "2", "3" plus dynamic "loopback-cap-<category>" gates.
    gates: dict[str, GateState] = Field(default_factory=_default_gates)
    inner_loop: InnerLoopState = Field(default_factory=InnerLoopState)
    loopback: LoopbackState = Field(default_factory=LoopbackState)
    review: ReviewState = Field(default_factory=ReviewState)
    terminology: TerminologyState = Field(default_factory=TerminologyState)
    agent_runs: list[AgentRun] = Field(default_factory=list)
    counters: dict[str, int] = Field(default_factory=_default_counters)
    last_error: LastError | None = None
    notify: NotifyTarget | None = None
    unattended: bool = False
    # Opt-in: send Prompt-06 search strings (never the disclosure) to Google Patents.
    search: bool = False
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def phase(self, phase: str) -> PhaseState:
        try:
            return self.phases[phase]
        except KeyError:
            raise StateError(f"unknown phase {phase!r}") from None

    def gate(self, key: str) -> GateState:
        """Gate state, creating loop-back-cap gates on demand (they are dynamic)."""
        if key not in self.gates:
            if not key.startswith("loopback-cap-"):
                raise StateError(f"unknown gate {key!r}")
            self.gates[key] = GateState()
        return self.gates[key]

    def bump(self, counter: str, by: int = 1) -> int:
        self.counters[counter] = self.counters.get(counter, 0) + by
        return self.counters[counter]

    def run(self, call_id: str) -> AgentRun | None:
        return next((r for r in self.agent_runs if r.call_id == call_id), None)


def new_state(slug: str, title: str, jurisdictions: list[str]) -> MatterState:
    """A fresh matter: every phase idle, every gate unrequested, all counters zero."""
    return MatterState(matter=MatterInfo(slug=slug, title=title, jurisdictions=list(jurisdictions)))


def load(path: Path | str) -> MatterState:
    """Read and validate `state.json`. A corrupt file is fatal, not something to patch."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StateError(f"no state file at {p}") from None
    except json.JSONDecodeError as exc:
        raise StateError(f"{p} is not valid JSON: {exc}") from exc
    try:
        return MatterState.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise StateError(f"{p} does not match the state schema: {exc}") from exc


def save(state: MatterState, path: Path | str) -> Path:
    """Atomic write.

    A run that dies mid-save must leave either the old state or the new one; a
    truncated `state.json` would make the matter unresumable.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = _now()
    payload = state.model_dump_json(indent=2) + "\n"

    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), prefix=".state-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return p


def repair_from_disk(dossier_dir: Path | str) -> MatterState:
    """Rebuild phases and agent runs from the artefacts on disk.

    Disk is the durable record: VERSION.json per phase version, meta.json per
    call. Human decisions (gates, terminology, loop-back counters) cannot be
    derived, so an existing state.json is kept for those.
    """
    root = Path(dossier_dir)
    state = _base_state_for_repair(root)

    for phase, dirname in PHASES.items():
        phase_state = PhaseState(agent=PHASE_AGENT[phase])
        previous = state.phases.get(phase)
        phase_dir = root / dirname
        for version_file in sorted(phase_dir.glob("v*/VERSION.json")):
            record = _read_version(version_file)
            if record is None:
                continue
            prior = previous.version(record.v) if previous else None
            if prior is not None and not record.prompts_done:
                record.prompts_done = prior.prompts_done
            phase_state.versions.append(record)
        phase_state.versions.sort(key=lambda rec: rec.v)
        latest = phase_state.latest_complete()
        running = [rec for rec in phase_state.versions if rec.status == "running"]
        if running:
            phase_state.status = "running"
            phase_state.current_version = max(rec.v for rec in running)
        elif latest is not None:
            phase_state.status = "complete"
            phase_state.current_version = latest.v
        elif phase_state.versions:
            phase_state.status = "failed"
            phase_state.current_version = max(rec.v for rec in phase_state.versions)
        state.phases[phase] = phase_state

    state.agent_runs = _read_runs(root / "runs")
    state.counters["calls"] = len(state.agent_runs)
    return state


def _base_state_for_repair(root: Path) -> MatterState:
    state_path = root / "state.json"
    if state_path.is_file():
        try:
            return load(state_path)
        except StateError as exc:
            log.warning("state.json unusable (%s); rebuilding from matter.json", exc)
    matter_path = root / "matter.json"
    if matter_path.is_file():
        data = json.loads(matter_path.read_text(encoding="utf-8"))
        return new_state(
            data.get("slug", root.name),
            data.get("title", root.name),
            list(data.get("jurisdictions", [])),
        )
    return new_state(root.name, root.name, [])


def _read_version(path: Path) -> VersionRecord | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("skipping unreadable %s: %s", path, exc)
        return None
    data.setdefault("dir", path.parent.name)
    data.setdefault("trigger", "unknown")
    try:
        return VersionRecord.model_validate(data)
    except Exception as exc:
        log.warning("skipping malformed %s: %s", path, exc)
        return None


def _read_runs(runs_dir: Path) -> list[AgentRun]:
    runs: list[AgentRun] = []
    if not runs_dir.is_dir():
        return runs
    for meta in sorted(runs_dir.glob("*/meta.json")):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("skipping unreadable %s: %s", meta, exc)
            continue
        data.setdefault("call_id", meta.parent.name)
        data.setdefault("agent_id", "unknown")
        try:
            runs.append(AgentRun.model_validate(data))
        except Exception as exc:
            log.warning("skipping malformed %s: %s", meta, exc)
    runs.sort(key=lambda r: (r.started_at, r.call_id))
    return runs


class Lock:
    """Exclusive, advisory lock on one dossier.

    Two runners in the same dossier would interleave version numbers and state
    writes. The lock file carries the owning pid so a crash leaves a *stale*
    lock that the next run can prove dead (`os.kill(pid, 0)`) and take over,
    rather than a mystery that needs manual deletion.
    """

    def __init__(self, dossier_dir: Path | str, name: str = "state.lock") -> None:
        self.dossier_dir = Path(dossier_dir)
        self.path = self.dossier_dir / name
        self.pid = os.getpid()
        self._held = False

    def acquire(self) -> Lock:
        self.dossier_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": self.pid, "at": _now(), "host": os.uname().nodename})
        try:
            self._create(payload)
        except FileExistsError:
            holder = self._holder_pid()
            if holder is not None and _pid_alive(holder):
                raise LockHeld(self.path, holder) from None
            log.warning("replacing stale lock %s (pid %s)", self.path, holder)
            self.path.unlink(missing_ok=True)
            try:
                self._create(payload)
            except FileExistsError:
                holder = self._holder_pid() or -1
                raise LockHeld(self.path, holder) from None
        self._held = True
        return self

    def release(self) -> None:
        """Only ever removes our own lock: a stale-lock takeover must not cascade."""
        if not self._held:
            return
        if self._holder_pid() == self.pid:
            self.path.unlink(missing_ok=True)
        self._held = False

    def _create(self, payload: str) -> None:
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)

    def _holder_pid(self) -> int | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return int(data["pid"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def __enter__(self) -> Lock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, owned by another user. Treat as held: refusing is the safe side.
        return True
    return True


def as_json(state: MatterState) -> dict[str, Any]:
    """JSON-safe dict for `status --json`."""
    return state.model_dump(mode="json")
