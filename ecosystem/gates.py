"""The three human gates, plus the loop-back cap gates.

A gate is two files and one state transition. `request()` writes
`gates/<stem>.pending.json`, stops the run and asks `ip-orchestrator` for a
plain-language brief; `decide()` validates the decision, writes
`gates/<stem>.decided.json` and applies the side effects that only a human may
authorise:

* **Gate 1** locks the terminology. Everything downstream is checked against it.
* **Gate 2** turns candidate embodiments into `CONFIRMED.md`. Phase 4 may only
  draft from confirmed material.
* **Gate 3** is the filing decision, and no agent may take it - `--by` is
  refused if it names one of the seven agents.

Decisions are idempotent: repeating the same decision returns `already_decided`
rather than re-running the side effects, and contradicting a recorded decision
is refused. A gate that has been re-requested (the Gate 2 delta after a Phase 3
loop-back) archives its previous decision first, so "the same decision" always
means "the same decision for the cycle now pending".
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ecosystem.config import AGENT_IDS, Settings
from ecosystem.contracts import deliverable_only
from ecosystem.dossier import Dossier, sha256_text, split_frontmatter
from ecosystem.log import EventLog
from ecosystem.state import ForcedDecision, MatterState
from ecosystem.terminology import parse_locked

log = logging.getLogger("ipfe")

__all__ = [
    "CAP_OPTIONS",
    "DEFAULT_DECISION",
    "GATE_OPTIONS",
    "GateError",
    "GateRefused",
    "GateResult",
    "Gates",
    "brief_path",
    "gate_stem",
    "is_cap_gate",
    "options_for",
]

GATE_OPTIONS: Final[dict[str, list[str]]] = {
    "1": ["accept", "reject"],
    "2": ["accept", "reject"],
    "3": ["accept", "reject"],
}
CAP_OPTIONS: Final[list[str]] = ["accept-residual", "manual-fix", "narrow", "abort"]

# What `status` offers as the ready-to-run command. Never destructive, and never
# the option that ends the matter.
DEFAULT_DECISION: Final[dict[str, str]] = {
    "1": "accept",
    "2": "accept",
    "3": "accept",
    "cap": "accept-residual",
}

_CAP_PREFIX: Final[str] = "loopback-cap-"


class GateError(RuntimeError):
    """The gate cannot be operated as asked (wrong state, missing artefact)."""


class GateRefused(GateError):
    """A decision this system will not record. Exit 2: the human must re-issue it."""


def is_cap_gate(gate: str) -> bool:
    return gate.startswith(_CAP_PREFIX)


def gate_stem(gate: str) -> str:
    """File stem: `gate-1` for the numbered gates, the key itself for cap gates."""
    return gate if is_cap_gate(gate) else f"gate-{gate}"


def brief_path(dossier: Dossier, gate: str) -> Path:
    return dossier.briefs_dir / f"{gate_stem(gate)}.md"


def options_for(gate: str) -> list[str]:
    if is_cap_gate(gate):
        return list(CAP_OPTIONS)
    try:
        return list(GATE_OPTIONS[gate])
    except KeyError:
        raise GateError(f"unknown gate {gate!r}") from None


def default_decision(gate: str) -> str:
    return DEFAULT_DECISION["cap" if is_cap_gate(gate) else gate]


class GateResult:
    """Outcome of `decide()`; `status` is `decided` or `already_decided`."""

    def __init__(self, gate: str, decision: str, status: str, effects: list[str]) -> None:
        self.gate = gate
        self.decision = decision
        self.status = status
        self.effects = effects

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"GateResult(gate={self.gate!r}, decision={self.decision!r}, status={self.status!r})"


class Gates:
    """Request and decide gates for one matter."""

    def __init__(
        self,
        dossier: Dossier,
        state: MatterState,
        settings: Settings,
        events: EventLog,
        brief_writer: Any = None,
    ) -> None:
        self.dossier = dossier
        self.state = state
        self.settings = settings
        self.events = events
        # Callable (gate, pending_payload) -> str. Injected by the runner so this
        # module never has to know about transports; absent in the CLI, which
        # only ever decides gates.
        self.brief_writer = brief_writer

    # -- request ------------------------------------------------------------

    def request(
        self,
        gate: str,
        payload: dict[str, Any] | None = None,
        blocks: list[str] | None = None,
    ) -> Path:
        """Open a gate: write the pending file, stop the run, write the brief."""
        options = options_for(gate)
        stem = gate_stem(gate)
        self._archive_previous_decision(gate)

        requested_at = _now()
        record: dict[str, Any] = {
            "gate": gate,
            "requested_at": requested_at,
            "payload": payload or {},
            "options": options,
            "blocks": list(blocks or []),
        }
        path = self.dossier.gates_dir / f"{stem}.pending.json"
        self.dossier.write_file(path, json.dumps(record, indent=2) + "\n", overwrite=True)

        gate_state = self.state.gate(gate)
        gate_state.status = "pending"
        gate_state.requested_at = requested_at
        gate_state.decided_at = None
        gate_state.decided_by = None
        gate_state.decision = None
        gate_state.blocks = list(blocks or [])
        gate_state.file = str(path.relative_to(self.dossier.root))
        # A cap gate is not a scheduled decision point; it is the run admitting
        # it cannot make progress, which is a different thing for the human.
        self.state.status = "blocked" if is_cap_gate(gate) else "awaiting_gate"

        self.events.emit(
            "gate_requested",
            gate=gate,
            options=options,
            blocks=blocks or [],
            file=str(path),
        )
        self._write_brief(gate, record)
        return path

    def _write_brief(self, gate: str, record: dict[str, Any]) -> Path:
        """Ask ip-orchestrator for the brief; never let that failure block the gate.

        The brief is a convenience for the human. The pending file is the
        contract. If the gateway is down, the human still gets a usable brief
        listing the files they need to read.
        """
        target = brief_path(self.dossier, gate)
        text = ""
        if self.brief_writer is not None:
            try:
                text = self.brief_writer(gate, record) or ""
            except Exception as exc:  # deliberately broad: a brief is never load-bearing
                log.warning("gate brief for %s failed (%s); writing the fallback", gate, exc)
                self.events.emit("gate_requested", gate=gate, brief_error=str(exc))
        if not text.strip():
            text = self._fallback_brief(gate, record)
        return self.dossier.write_file(target, text.rstrip() + "\n", overwrite=True)

    def _fallback_brief(self, gate: str, record: dict[str, Any]) -> str:
        payload = record.get("payload") or {}
        files = payload.get("files") or []
        lines = [
            f"# Gate {gate}",
            "",
            "*(Automatic brief: the orchestrator could not be reached, so this is a "
            "plain listing rather than a written brief. The files below are the "
            "material this gate is about.)*",
            "",
            "## What you need to read",
            "",
        ]
        if files:
            lines.extend(f"- `{item}`" for item in files)
        else:
            lines.append("- (no files recorded for this gate)")
        blocks = [f"- {item}" for item in record.get("blocks", [])] or ["- (nothing recorded)"]
        lines += [
            "",
            "## Options",
            "",
            *[f"- `{option}`" for option in record.get("options", [])],
            "",
            "## What this blocks",
            "",
            *blocks,
        ]
        if payload.get("summary"):
            lines += ["", "## Summary", "", str(payload["summary"])]
        return "\n".join(lines)

    def pending(self, gate: str) -> dict[str, Any] | None:
        path = self.dossier.gates_dir / f"{gate_stem(gate)}.pending.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GateError(f"{path} is not valid JSON: {exc}") from exc
        return data if isinstance(data, dict) else None

    def pending_gates(self) -> list[str]:
        return [key for key, value in self.state.gates.items() if value.status == "pending"]

    # -- decide -------------------------------------------------------------

    def decide(
        self,
        gate: str,
        decision: str,
        by: str,
        note: str | None = None,
        answers_file: Path | str | None = None,
        confirmed_file: Path | str | None = None,
    ) -> GateResult:
        """Record a human decision and apply its side effects. Idempotent."""
        options = options_for(gate)
        if decision not in options:
            raise GateRefused(
                f"decision {decision!r} is not valid for gate {gate}; "
                f"expected one of {', '.join(options)}"
            )
        if not (by or "").strip():
            raise GateRefused("--by must name the person taking the decision")
        if by.strip() in AGENT_IDS:
            raise GateRefused(
                f"--by {by!r} names an agent in this system; a gate decision is a "
                "human decision and must be recorded under a human's name"
            )

        gate_state = self.state.gate(gate)
        if gate_state.status == "decided":
            if gate_state.decision == decision:
                return GateResult(gate, decision, "already_decided", [])
            raise GateRefused(
                f"gate {gate} was already decided {gate_state.decision!r} by "
                f"{gate_state.decided_by!r} at {gate_state.decided_at}; refusing to "
                f"overwrite it with {decision!r}"
            )
        if gate_state.status != "pending" or self.pending(gate) is None:
            raise GateError(
                f"gate {gate} is not pending (status {gate_state.status!r}); "
                "nothing is waiting for this decision"
            )

        effects = self._apply(gate, decision, by, note, answers_file, confirmed_file)

        decided_at = _now()
        gate_state.status = "decided"
        gate_state.decision = decision
        gate_state.decided_at = decided_at
        gate_state.decided_by = by
        gate_state.note = note
        record = {
            "gate": gate,
            "decision": decision,
            "by": by,
            "note": note,
            "decided_at": decided_at,
            "effects": effects,
            "pending": self.pending(gate),
        }
        path = self.dossier.gates_dir / f"{gate_stem(gate)}.decided.json"
        self.dossier.write_file(path, json.dumps(record, indent=2) + "\n", overwrite=True)
        gate_state.file = str(path.relative_to(self.dossier.root))
        (self.dossier.gates_dir / f"{gate_stem(gate)}.pending.json").unlink(missing_ok=True)

        self.events.emit(
            "gate_decided", gate=gate, decision=decision, by=by, note=note, effects=effects
        )
        return GateResult(gate, decision, "decided", effects)

    # -- side effects -------------------------------------------------------

    def _apply(
        self,
        gate: str,
        decision: str,
        by: str,
        note: str | None,
        answers_file: Path | str | None,
        confirmed_file: Path | str | None,
    ) -> list[str]:
        if is_cap_gate(gate):
            return self._apply_cap(gate, decision, by, note, confirmed_file)
        if decision == "reject":
            self.state.status = "failed"
            return [f"gate {gate} rejected: {note or 'no note given'}"]
        if gate == "1":
            return self._apply_gate1(answers_file)
        if gate == "2":
            return self._apply_gate2(confirmed_file)
        if gate == "3":
            self.state.status = "complete"
            self.state.cursor.phase = "PACKAGE"
            self.state.cursor.step = "complete"
            return ["matter marked complete"]
        raise GateError(f"unknown gate {gate!r}")

    # Gate 1 -----------------------------------------------------------------

    def _apply_gate1(self, answers_file: Path | str | None) -> list[str]:
        effects: list[str] = []
        target = self.dossier.intake_dir / "gate-1-answers.md"
        if answers_file is not None:
            source = Path(answers_file).expanduser()
            if not source.is_file():
                raise GateRefused(f"--answers-file does not exist: {source}")
            shutil.copy2(source, target)
            effects.append(f"copied {source} -> {target.relative_to(self.dossier.root)}")
        elif not target.is_file():
            # Phases 2 and 3 declare `intake:gate-1-answers` as a required input.
            # Deciding Gate 1 without answering anything is legitimate, so the
            # file is always written - saying plainly that nothing was answered
            # rather than leaving the agents to infer it from a missing file.
            self.dossier.write_file(target, self._unanswered_questions())
            effects.append("wrote 00-intake/gate-1-answers.md (no answers supplied)")

        locked = self.dossier.locked_terminology_path
        if locked.is_file():
            # The human edited the table before deciding. Theirs wins: the point
            # of the gate is that a person owns the vocabulary.
            effects.append("kept the existing terminology/LOCKED.md (human-edited)")
            source_label = "human-edited LOCKED.md"
        else:
            output = self.dossier.latest_output("P1", "04")
            if output is None:
                raise GateError(
                    "cannot lock terminology: no Prompt 04 output in a complete P1 version"
                )
            _, body = split_frontmatter(output.read_text(encoding="utf-8"))
            table = deliverable_only(body)
            if not table:
                raise GateError(f"{output} has no `## Deliverable` section to lock")
            # No dossier path in the header: this file is embedded in every
            # later prompt, including the blind reviewer's, and the reviewer has
            # no business learning which Phase 1 output produced it. The
            # provenance is in state.terminology.locked_from and in
            # gates/gate-1.decided.json.
            header = (
                "<!-- Locked at Gate 1 from the Prompt 04 terminology table. "
                "Edit this file before deciding Gate 1 if the table is wrong; "
                "after the lock it is the vocabulary every agent must use. -->\n\n"
            )
            self.dossier.write_file(locked, header + table + "\n")
            source_label = str(output.relative_to(self.dossier.root))
            effects.append(f"wrote terminology/LOCKED.md from {source_label}")

        text = locked.read_text(encoding="utf-8")
        digest = sha256_text(text)
        try:
            table_model = parse_locked(text, source_sha256=digest)
        except ValueError as exc:
            raise GateError(
                f"terminology/LOCKED.md cannot be parsed ({exc}); fix the table and "
                "re-record the decision"
            ) from exc
        self.dossier.write_file(
            self.dossier.terminology_dir / "LOCKED.json",
            table_model.to_json() + "\n",
            overwrite=True,
        )
        self.state.terminology.locked_at = _now()
        self.state.terminology.locked_from = source_label
        self.state.terminology.sha256 = digest
        self.state.terminology.terms = len(table_model)
        effects.append(f"locked {len(table_model)} terms")
        self.state.status = "idle"
        return effects

    def _unanswered_questions(self) -> str:
        """A truthful stand-in for answers the human did not give."""
        questions: list[str] = []
        version_dir = self.dossier.latest("P1")
        if version_dir is not None:
            for envelope_path in sorted(version_dir.path.glob("*.envelope.json")):
                try:
                    data = json.loads(envelope_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for question in data.get("open_questions") or []:
                    if isinstance(question, dict):
                        questions.append(
                            f"- **{question.get('id', '?')}** "
                            f"({envelope_path.name.split('.')[0]}): "
                            f"{question.get('text', '')}  \n  *Not answered.*"
                        )
        body = "\n".join(questions) or "- (Phase 1 recorded no open questions.)"
        return (
            "# Gate 1 answers\n\n"
            "The human accepted Gate 1 without supplying answers. Every question "
            "below is still open: treat it as unanswered, do not invent an answer, "
            "and record anything that depends on it as an assumption.\n\n"
            f"{body}\n"
        )

    # Gate 2 -----------------------------------------------------------------

    def _apply_gate2(self, confirmed_file: Path | str | None) -> list[str]:
        version_dir = self._p3_version()
        target = version_dir / "CONFIRMED.md"
        effects: list[str] = []

        if confirmed_file is not None:
            source = Path(confirmed_file).expanduser()
            if not source.is_file():
                raise GateRefused(f"--confirmed-file does not exist: {source}")
            text = source.read_text(encoding="utf-8")
            effects.append(f"CONFIRMED.md taken verbatim from {source}")
        else:
            text, used = self._confirmed_from_outputs(version_dir)
            effects.append(f"CONFIRMED.md assembled from {', '.join(used) or 'nothing'}")

        self.dossier.write_file(target, text.rstrip() + "\n", overwrite=True)
        flipped = self._flip_p3_status(version_dir)
        if flipped:
            effects.append(f"marked {', '.join(flipped)} confirmed")
        self.state.status = "idle"
        return effects

    def _p3_version(self) -> Path:
        current = self.state.phase("P3").current_version
        version_dir = (
            self.dossier.version("P3", current) if current else None
        ) or self.dossier.latest("P3")
        if version_dir is None:
            raise GateError("cannot confirm embodiments: no P3 version exists")
        return version_dir.path

    def _confirmed_from_outputs(self, version_dir: Path) -> tuple[str, list[str]]:
        """Concatenate the Deliverables of this P3 version, newest layer on top.

        A delta re-confirmation after a Phase 3 loop-back only contains
        `x-loopback-p3`; the previously confirmed material is carried forward so
        `CONFIRMED.md` always remains the whole confirmed record.
        """
        parts: list[str] = [
            f"# Confirmed embodiments ({version_dir.name})",
            "",
            "Confirmed by a human at Gate 2. Phase 4 may draft only from this file.",
        ]
        used: list[str] = []
        previous = self._previous_confirmed(version_dir)
        for path in sorted(version_dir.glob("*.md")):
            if path.name in {"CONFIRMED.md"}:
                continue
            frontmatter, body = split_frontmatter(path.read_text(encoding="utf-8"))
            deliverable = deliverable_only(body)
            if not deliverable:
                continue
            prompt_id = str(frontmatter.get("prompt") or path.stem.split("-")[0])
            parts += ["", f"## {prompt_id} - {path.stem}", "", deliverable]
            used.append(prompt_id)
        if previous:
            parts += ["", "## Previously confirmed", "", previous]
            used.append("previous CONFIRMED.md")
        return "\n".join(parts), used

    def _previous_confirmed(self, current: Path) -> str:
        for version_dir in reversed(self.dossier.versions("P3")):
            if version_dir.path == current:
                continue
            candidate = version_dir.path / "CONFIRMED.md"
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()
        return ""

    def _flip_p3_status(self, version_dir: Path) -> list[str]:
        flipped: list[str] = []
        for path in sorted(version_dir.glob("*.md")):
            if path.name == "CONFIRMED.md":
                continue
            frontmatter, _ = split_frontmatter(path.read_text(encoding="utf-8"))
            if not frontmatter or frontmatter.get("status") == "confirmed":
                continue
            self.dossier.mark_status(path, "confirmed")
            flipped.append(str(frontmatter.get("prompt") or path.stem))
        return flipped

    # Cap gates --------------------------------------------------------------

    def _apply_cap(
        self,
        gate: str,
        decision: str,
        by: str,
        note: str | None,
        confirmed_file: Path | str | None,
    ) -> list[str]:
        category = gate[len(_CAP_PREFIX) :]
        effects: list[str] = []
        # `applied` is consumed by the runner on the next `run`; the decision is
        # recorded either way so the cover memo can quote what the human chose.
        forced = ForcedDecision(
            category=category, decision=decision, decided_by=by, note=note, applied=False
        )

        if decision == "abort":
            self.state.status = "failed"
            effects.append(f"matter aborted at the {category} loop-back cap")
        elif decision == "accept-residual":
            self.state.status = "idle"
            effects.append(
                f"{category} findings accepted as residual; they go to "
                "residual-findings.md and the run continues to packaging"
            )
        elif decision == "manual-fix":
            self.state.status = "idle"
            effects.extend(self._manual_fix(confirmed_file))
            effects.append("one extra re-review authorised")
        elif decision == "narrow":
            self.state.status = "idle"
            effects.append(
                "one authorised claim narrowing (x-p5-revise) will run before re-review"
            )
        self.state.loopback.forced_decisions.append(forced)
        self.events.emit(
            "loopback_cap_reached",
            category=category,
            decision=decision,
            by=by,
            note=note,
        )
        return effects

    def _manual_fix(self, confirmed_file: Path | str | None) -> list[str]:
        """Carry the human's edits into new versions so they are recorded, not lost.

        The human edits `description.md` / `claims.md` in the latest version
        directory (the one escape hatch from immutability). Copying them forward
        into a `manual-fix` version restores the invariant: the current text is
        always the newest complete version, and the edit is attributable.
        """
        effects: list[str] = []
        override = Path(confirmed_file).expanduser() if confirmed_file else None
        if override is not None and not override.is_file():
            raise GateRefused(f"--confirmed-file does not exist: {override}")

        for phase, filename in (("P4", "description.md"), ("P5", "claims.md")):
            source = self._latest_file(phase, filename)
            if source is None:
                continue
            text = (
                override.read_text(encoding="utf-8")
                if override is not None and override.name == filename
                else source.read_text(encoding="utf-8")
            )
            version = self.dossier.new_version(phase, "manual-fix")
            self.dossier.write_file(version.path / filename, text)
            self.dossier.complete_version(phase, version.v)
            phase_state = self.state.phase(phase)
            phase_state.current_version = version.v
            phase_state.status = "complete"
            phase_state.versions.append(
                _version_record(version.v, version.path.name, "manual-fix", [filename])
            )
            effects.append(f"{filename} carried into {phase} v{version.v} (manual-fix)")
        return effects

    def _latest_file(self, phase: str, filename: str) -> Path | None:
        for version_dir in reversed(self.dossier.versions(phase)):
            candidate = version_dir.path / filename
            if candidate.is_file():
                return candidate
        return None

    # -- re-request ----------------------------------------------------------

    def _archive_previous_decision(self, gate: str) -> None:
        """Keep the record of a gate that is being asked again (the Gate 2 delta)."""
        stem = gate_stem(gate)
        decided = self.dossier.gates_dir / f"{stem}.decided.json"
        if not decided.is_file():
            return
        existing = sorted(self.dossier.gates_dir.glob(f"{stem}.decided.r*.json"))
        decided.rename(self.dossier.gates_dir / f"{stem}.decided.r{len(existing) + 1}.json")
        log.info("gate %s re-requested; previous decision archived", gate)


def _version_record(v: int, dirname: str, trigger: str, prompts: list[str]) -> Any:
    from ecosystem.state import VersionRecord

    return VersionRecord(
        v=v,
        dir=dirname,
        trigger=trigger,
        prompts_done=prompts,
        status="complete",
        finished_at=_now(),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
