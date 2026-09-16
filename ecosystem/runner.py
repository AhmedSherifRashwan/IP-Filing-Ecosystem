"""The pipeline itself: phases, gates, the 5-6 inner loop and the loop-back engine.

Everything the playbook makes advisory, this module makes deterministic. Caps
are counters, gates are files, sequencing is a list. No agent decides what runs
next; `ip-orchestrator` is called only for prose a human reads.

The run is a sequence of stages, each one idempotent:

    P1 -> Gate 1 -> P2 -> P3 -> Gate 2 -> inner loop -> review -> package -> Gate 3

A stage that is already complete returns immediately, which is what makes
`resume` work: the runner never remembers anything it has not written to
`state.json` first. Every call persists state before the next one starts, so a
process killed between two calls resumes at exactly the next call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from ecosystem import loopback, package
from ecosystem.calls import AttemptContext, Caller, CallResult, section_body
from ecosystem.config import Settings
from ecosystem.contracts import Correction, Finding
from ecosystem.dossier import Dossier, InputContext, VersionDir
from ecosystem.gates import Gates
from ecosystem.log import EventLog
from ecosystem.playbook import Playbook, PromptSpec
from ecosystem.prompt_builder import synthetic_input
from ecosystem.state import (
    InnerLoopRound,
    LoopbackEvent,
    MatterState,
    ReviewVersion,
    VersionRecord,
)
from ecosystem.state import save as save_state
from ecosystem.transport import Transport

log = logging.getLogger("ipfe")

__all__ = ["DryRunReport", "DryRunRow", "Runner", "StopReason"]


class StopReason(StrEnum):
    """Why `run()` returned. The CLI maps these onto exit codes (plan s3.6)."""

    DONE = "done"
    GATE = "gate"
    CAP = "cap"
    UNTIL = "until"
    DRY_RUN = "dry-run"
    FAILED = "failed"


# Phase prompt lists. These are the playbook's sequences, and they are the only
# place the order is written down.
P1_PROMPTS: Final[tuple[str, ...]] = ("01", "02", "03", "04", "05")
P2_PROMPTS: Final[tuple[str, ...]] = ("06", "07", "08", "09", "10")
P3_PROMPTS: Final[tuple[str, ...]] = ("11", "12", "13", "14", "15")

# Inner loop, round 1: the specification is written, then claimed.
P4_ROUND1: Final[tuple[str, ...]] = (
    "16",
    "17",
    "18",
    "19",
    "20",
    "20a",
    "x-p4-compose-description",
)
P5_ROUND1: Final[tuple[str, ...]] = ("21", "22", "23", "24", "25", "26", "27")
# Round 2+: revise, do not restart.
P4_REVISE: Final[tuple[str, ...]] = ("x-p4-revise", "17", "20a")
P5_REVISE: Final[tuple[str, ...]] = ("x-p5-revise", "25")

REVIEW_PROMPTS: Final[tuple[str, ...]] = (
    "28",
    "29",
    "30",
    "31",
    "32",
    "33",
    "34",
    "35",
    "36",
    "37",
    "38",
    "40a",
)
# Re-run after a formal fix; stored as `<id>r` so the originals stay readable
# and the aggregator knows which envelope supersedes which.
FORMAL_RECHECK: Final[tuple[str, ...]] = ("37", "38", "40a")
# Run once the review is clean, against the final text.
CLEAN_PROMPTS: Final[tuple[str, ...]] = ("39", "gate14", "40")

STAGES: Final[tuple[str, ...]] = (
    "P1",
    "gate1",
    "P2",
    "P3",
    "gate2",
    "inner",
    "review",
    "package",
    "gate3",
)

# `--until X` stops after this stage.
UNTIL_STAGE: Final[dict[str, str]] = {
    "gate1": "gate1",
    "gate2": "gate2",
    "review": "review",
    "package": "package",
}

# `--from X` re-enters here. "review" re-opens a review round.
FROM_STAGE: Final[dict[str, str]] = {
    "P1": "P1",
    "P2": "P2",
    "P3": "P3",
    "P4": "inner",
    "P5": "inner",
    "inner": "inner",
    "review": "review",
    "package": "package",
}


@dataclass
class DryRunRow:
    prompt: str
    chars: int
    transport: str
    inputs: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    prompt_path: Path | None = None


@dataclass
class DryRunReport:
    stage: str
    rows: list[DryRunRow] = field(default_factory=list)

    def table(self) -> str:
        """`prompt | chars | transport | inputs`, as the plan's verification step asks."""
        header = ("prompt", "chars", "transport", "inputs")
        body = [
            (
                row.prompt,
                f"{row.chars:,}",
                row.transport,
                ", ".join(row.inputs)
                + (f"  [missing: {', '.join(row.missing)}]" if row.missing else ""),
            )
            for row in self.rows
        ]
        widths = [
            max(len(header[i]), *(len(line[i]) for line in body)) if body else len(header[i])
            for i in range(4)
        ]
        lines = [" | ".join(header[i].ljust(widths[i]) for i in range(4))]
        lines.append("-+-".join("-" * widths[i] for i in range(4)))
        lines += [" | ".join(line[i].ljust(widths[i]) for i in range(4)) for line in body]
        return "\n".join(lines)


class Runner:
    """Drives one matter from where it is to the next thing a human must do."""

    def __init__(
        self,
        dossier: Dossier,
        state: MatterState,
        playbook: Playbook,
        transport: Transport,
        settings: Settings,
        events: EventLog,
        allow_truncate: bool = False,
    ) -> None:
        self.dossier = dossier
        self.state = state
        self.playbook = playbook
        self.settings = settings
        self.events = events
        self.allow_truncate = allow_truncate
        self.caller = Caller(dossier, state, playbook, transport, settings, events)
        self.gates = Gates(dossier, state, settings, events, brief_writer=self._gate_brief)
        self.dry_run_report: DryRunReport | None = None
        self._force_from: str | None = None
        self._inner_loop_flags: str | None = None

    # -- entry point --------------------------------------------------------

    def run(
        self,
        until: str | None = None,
        from_phase: str | None = None,
        dry_run: bool = False,
    ) -> StopReason:
        """Advance the matter. Returns why it stopped; never raises on a gate."""
        if until is not None and until not in UNTIL_STAGE:
            raise ValueError(f"--until must be one of {', '.join(UNTIL_STAGE)}")
        if from_phase is not None and from_phase not in FROM_STAGE:
            raise ValueError(f"--from must be one of {', '.join(FROM_STAGE)}")

        if dry_run:
            self.dry_run_report = self._dry_run(from_phase)
            return StopReason.DRY_RUN

        if self.state.status == "complete":
            # Gate 3 is the only thing that sets this, and only a human sets
            # Gate 3. A finished matter is not re-opened by running the CLI again.
            self.events.emit("run_stopped", reason="done", status="complete")
            return StopReason.DONE

        self._force_from = FROM_STAGE[from_phase] if from_phase else None
        stop_stage = UNTIL_STAGE[until] if until else None

        self.events.emit(
            "run_started",
            until=until,
            from_phase=from_phase,
            cursor=self.state.cursor.model_dump(),
        )
        self.state.status = "running"
        self._save()

        try:
            reason = self._drive(stop_stage)
        except Exception as exc:
            self.state.status = "failed"
            self.state.last_error = _last_error(exc, self.state)
            self._save()
            self.events.emit(
                "run_failed", error=f"{type(exc).__name__}: {exc}", phase=self.state.cursor.phase
            )
            raise

        if reason is not StopReason.FAILED:
            # The run got where it was going. A `last_error` from an earlier
            # attempt is history, and `status` must not present it as current.
            self.state.last_error = None
        if reason is StopReason.DONE and self.state.status == "running":
            self.state.status = "idle"
        self._save()
        self.events.emit("run_stopped", reason=reason.value, status=self.state.status)
        return reason

    def _drive(self, stop_stage: str | None) -> StopReason:
        started = False
        for stage in STAGES:
            if self._force_from is not None and not started:
                if stage != self._force_from:
                    continue
                started = True
            reason = self._stage(stage)
            if reason is not None:
                return reason
            if stop_stage is not None and stage == stop_stage:
                return StopReason.UNTIL
        return StopReason.DONE

    def _stage(self, stage: str) -> StopReason | None:
        handlers = {
            "P1": lambda: self._phase_stage("P1", P1_PROMPTS),
            "gate1": lambda: self._gate_stage("1"),
            "P2": lambda: self._phase_stage("P2", P2_PROMPTS),
            "P3": lambda: self._phase_stage("P3", P3_PROMPTS),
            "gate2": lambda: self._gate_stage("2"),
            "inner": self._inner_loop,
            "review": self._review_stage,
            "package": self._package_stage,
            "gate3": lambda: self._gate_stage("3"),
        }
        self.state.cursor.step = stage
        return handlers[stage]()

    # -- phases -------------------------------------------------------------

    def _phase_stage(self, phase: str, prompts: tuple[str, ...]) -> StopReason | None:
        phase_state = self.state.phase(phase)
        forced = self._force_from == FROM_STAGE.get(phase)
        if phase_state.status == "complete" and not forced:
            return None
        self.events.emit("phase_started", phase=phase, prompts=list(prompts))
        version = self._open_version(phase, "initial" if not forced else "manual-rerun")
        self._run_prompts(phase, version, prompts)
        self._complete_version(phase, version)
        # A forced re-entry applies once; after that the run behaves normally.
        self._force_from = None
        return None

    def _run_prompts(
        self,
        phase: str,
        version: VersionDir,
        prompts: tuple[str, ...],
        ctx_factory: Any = None,
        slot: str | None = None,
        output_ids: dict[str, str] | None = None,
    ) -> list[CallResult]:
        """Run a list of prompts into one version, skipping what is already there."""
        record = self._version_record(phase, version)
        results: list[CallResult] = []
        for prompt_id in prompts:
            output_id = (output_ids or {}).get(prompt_id, prompt_id)
            if output_id in record.prompts_done or _output_exists(version.path, output_id):
                log.info("skipping %s: already in %s", output_id, version.path.name)
                continue
            spec = self.playbook.get(prompt_id)
            ctx = ctx_factory(spec) if ctx_factory else AttemptContext()
            ctx.allow_truncate = ctx.allow_truncate or self.allow_truncate
            if slot is not None and ctx.slot is None:
                ctx.slot = slot
            if output_id != prompt_id:
                ctx.output_prompt_id = output_id
            self.state.cursor.phase = phase
            result = self.caller.execute(spec, version.v, ctx)
            results.append(result)
            record.prompts_done.append(output_id)
            self._save()
        return results

    # -- gates --------------------------------------------------------------

    def _gate_stage(self, gate: str) -> StopReason | None:
        gate_state = self.state.gate(gate)
        if gate_state.status == "decided":
            if gate_state.decision == "reject":
                self.state.status = "failed"
                return StopReason.FAILED
            return None
        if gate_state.status != "pending":
            payload, blocks = self._gate_payload(gate)
            self.gates.request(gate, payload, blocks)
            self._save()
        return StopReason.GATE

    def _gate_payload(self, gate: str) -> tuple[dict[str, Any], list[str]]:
        if gate == "1":
            files = self._phase_files("P1")
            return (
                {
                    "summary": "Phase 1 is complete. Confirm the extracted disclosure and "
                    "lock the terminology table before any other phase runs.",
                    "files": files,
                    "locks": "terminology/LOCKED.md is created from the Prompt 04 table "
                    "as it stands; edit that file first if the table is wrong.",
                    "open_questions": self._open_questions("P1"),
                },
                ["P2", "P3", "P4", "P5"],
            )
        if gate == "2":
            delta = self.state.gate("2").decision is not None
            return (
                {
                    "summary": (
                        "Phase 3 produced additional candidate embodiments in response to "
                        "the reviewer's findings. Confirm which of them the inventors "
                        "actually conceived."
                        if delta
                        else "Phase 3 is complete. Every embodiment below is a candidate "
                        "until you confirm it; Phase 4 may only draft from confirmed "
                        "material."
                    ),
                    "files": self._phase_files("P3"),
                    "delta": delta,
                },
                ["P4", "P5"],
            )
        if gate == "3":
            return (
                {
                    "summary": "The package is assembled. No agent in this system may "
                    "authorise filing; this decision records that a human has read it.",
                    "files": self._package_files(),
                },
                ["filing"],
            )
        category = gate.removeprefix("loopback-cap-")
        return (
            {
                "summary": f"The {category} loop-back cap has been reached and "
                "critical or high findings are still open.",
                "category": category,
                "cycles": self.state.loopback.cycles.get(category, 0),
                "files": self._review_files(),
                "findings": self._open_finding_summaries(category),
            },
            ["packaging"],
        )

    def _gate_brief(self, gate: str, record: dict[str, Any]) -> str:
        """Ask ip-orchestrator for the brief. Failure is the caller's to swallow."""
        spec = self.playbook.get("x-gate-brief")
        stem = gate if gate.startswith("loopback-cap-") else f"gate-{gate}"
        round_no = len(list(self.dossier.gates_dir.glob(f"{stem}.decided.r*.json"))) + 1
        pending = synthetic_input(
            spec=f"gate:{gate}",
            label=f"gates/{stem}.pending.json",
            text=_pending_text(gate, record),
        )
        ctx = AttemptContext(
            extra_inputs=[pending],
            slot=f"gate-{gate}",
            write_output=False,
            allow_truncate=True,
            input_ctx=InputContext(include_rationale=True),
        )
        result = self.caller.execute(spec, round_no, ctx)
        self._save()
        return result.reply_text

    # -- inner loop (plan s3.8) ---------------------------------------------

    def _inner_loop(self) -> StopReason | None:
        loop = self.state.inner_loop
        loop.cap = self.settings.inner_loop_cap
        if loop.converged and self._force_from != "inner":
            return None
        if self._force_from == "inner":
            loop.converged = False
            self._force_from = None

        while not loop.converged and loop.round < loop.cap:
            round_no = loop.round + 1
            self.events.emit("inner_loop_round", round=round_no, phase="P4")
            self.state.cursor.inner_round = round_no

            p4_prompts = P4_ROUND1 if round_no == 1 else P4_REVISE
            p4_version = self._open_version("P4", _trigger("inner-loop", round_no))
            self._run_prompts("P4", p4_version, p4_prompts, self._inner_ctx)
            self._complete_version("P4", p4_version)

            p5_prompts = list(P5_ROUND1 if round_no == 1 else P5_REVISE)
            p5_version = self._open_version("P5", _trigger("inner-loop", round_no))
            self._run_prompts("P5", p5_version, tuple(p5_prompts), self._inner_ctx)
            if round_no > 1 and self._functional_flag(p5_version) and "26" not in p5_prompts:
                # The claims drafter flagged means-plus-function exposure; the
                # audit that judges it is cheap and the consequence of skipping
                # it is a 112(f) construction nobody intended.
                self._run_prompts("P5", p5_version, ("26",), self._inner_ctx)
            self._complete_version("P5", p5_version)

            requests = self._signals("P5", p5_version, "disclosure_requests")
            disagreements = self._signals("P4", p4_version, "open_disagreements")
            loop.round = round_no
            loop.rounds.append(
                InnerLoopRound(
                    round=round_no,
                    finished_at=_now(),
                    disclosure_requests=len(requests),
                    open_disagreements=len(disagreements),
                )
            )
            loop.converged = not requests and not disagreements
            self.events.emit(
                "inner_loop_round",
                round=round_no,
                converged=loop.converged,
                disclosure_requests=len(requests),
                open_disagreements=len(disagreements),
            )
            self.caller.cross_check()
            self._save()

        if not loop.converged:
            # The package still goes to the reviewer; the flags do not. They are
            # for the human, and they say what the two drafters could not settle.
            self._inner_loop_flags = self._flags_text()
            log.warning("inner loop hit its cap of %d rounds without converging", loop.cap)
        return None

    def _inner_ctx(self, spec: PromptSpec) -> AttemptContext:
        return AttemptContext(input_ctx=InputContext(phase=spec.phase, prompt_id=spec.id))

    def _functional_flag(self, version: VersionDir) -> bool:
        """Heuristic (documented in prompts/README.md): the claims drafter mentions
        functional language in an open disagreement or a support reference.

        The envelope has no dedicated field for this, so the runner reads the
        words the drafter used. False positives cost one cheap audit call; false
        negatives cost claim scope, so the heuristic is deliberately loose.
        """
        envelope = _read_envelope(version.path / "x-p5-revise.envelope.json")
        if envelope is None:
            return False
        blobs = [
            str(item) for item in (envelope.get("open_disagreements") or [])
        ] + [str(item) for item in (envelope.get("support_refs") or [])]
        return any("functional" in blob.lower() for blob in blobs)

    def _signals(self, phase: str, version: VersionDir, key: str) -> list[Any]:
        """Union of one envelope list across a version.

        The plan says "the latest envelope"; the union is stricter and less
        ambiguous - a disclosure request raised by Prompt 25 and ignored by
        Prompt 26 is still an unmet request.
        """
        out: list[Any] = []
        for envelope_path in sorted(version.path.glob("*.envelope.json")):
            data = _read_envelope(envelope_path)
            if data is None:
                continue
            value = data.get(key)
            if isinstance(value, list):
                out.extend(value)
        return out

    def _flags_text(self) -> str:
        p4 = self.dossier.latest("P4")
        p5 = self.dossier.latest("P5")
        requests = self._signals("P5", p5, "disclosure_requests") if p5 else []
        disagreements = self._signals("P4", p4, "open_disagreements") if p4 else []
        lines = [
            "# Inner-loop flags",
            "",
            f"The Phase 4 / Phase 5 loop reached its cap of {self.state.inner_loop.cap} "
            "rounds without converging. The package went to the independent reviewer "
            "anyway; this file did not. These are the points the specification drafter "
            "and the claims drafter could not settle between them.",
            "",
            "## Disclosure the claims drafter asked for and did not get",
            "",
        ]
        lines += [f"- {_describe(item)}" for item in requests] or ["- None."]
        lines += ["", "## Disagreements still open", ""]
        lines += [f"- {_describe(item)}" for item in disagreements] or ["- None."]
        return "\n".join(lines)

    # -- review + loop-back (plan s3.9) -------------------------------------

    def _review_stage(self) -> StopReason | None:
        if self._force_from == "review":
            self._force_from = None
            self._close_running("REVIEW")
        elif self.state.phase("PACKAGE").status == "complete":
            # The package was built from a clean review. Re-reviewing it would
            # produce a new review version of text nobody has changed.
            return None

        while True:
            reason = self._apply_forced_decisions()
            if reason is not None:
                return reason

            active = self._active_route()
            if active is not None:
                reason = self._run_route(active)
                if reason is not None:
                    return reason
                self._close_running("REVIEW")

            version = self._review_round()
            findings = loopback.aggregate(version.path)
            self._record_review(version, findings)

            self._formal_pass(version)
            findings = loopback.aggregate(version.path)
            self._record_review(version, findings)

            plan = self._plan(findings)
            if plan.clean:
                self.events.emit(
                    "loopback_routed",
                    phase="REVIEW",
                    routed=[],
                    counts=loopback.counts(findings),
                )
                return None
            if plan.capped:
                category = plan.capped[0]
                self.events.emit(
                    "loopback_cap_reached",
                    category=category,
                    cycles=self.state.loopback.cycles.get(category, 0),
                    findings=plan.finding_ids.get(category, []),
                )
                self.gates.request(*self._cap_gate_args(category))
                self._save()
                return StopReason.CAP

            event = self._start_route(plan, version)
            reason = self._run_route(event)
            if reason is not None:
                return reason
            self._close_running("REVIEW")

    def _plan(self, findings: list[Finding]) -> loopback.RoutePlan:
        plan = loopback.plan_routes(findings, self.state, self.settings)
        accepted = {
            decision.category
            for decision in self.state.loopback.forced_decisions
            if decision.decision == "accept-residual"
        }
        if accepted:
            # A human has taken responsibility for these; re-routing them would
            # overrule a decision the system explicitly asked for.
            plan.categories = [c for c in plan.categories if c not in accepted]
            plan.capped = [c for c in plan.capped if c not in accepted]
            if not plan.categories:
                plan.steps = []
                plan.gate2_reconfirm = False
        return plan

    def _review_round(self) -> VersionDir:
        """Build the package and run the twelve review prompts into one version."""
        version = self._open_version("REVIEW", self._review_trigger())
        if self._inner_loop_flags:
            self.dossier.write_file(
                version.path / "inner-loop-flags.md",
                self._inner_loop_flags,
                overwrite=True,
            )
            self._inner_loop_flags = None
        self.caller.cross_check()
        package_dir = package.build_review_package(self.dossier, self.state, version.v)
        self.events.emit("package_built", phase="REVIEW", path=package_dir, kind="review")
        self.events.emit("phase_started", phase="REVIEW", prompts=list(REVIEW_PROMPTS))
        self._run_prompts(
            "REVIEW", version, REVIEW_PROMPTS, self._review_ctx(package_dir, version.v)
        )
        return version

    def _review_ctx(self, package_dir: Path, review_version: int) -> Any:
        def factory(spec: PromptSpec) -> AttemptContext:
            return AttemptContext(
                package_dir=package_dir,
                input_ctx=InputContext(
                    phase=spec.phase,
                    prompt_id=spec.id,
                    package_dir=package_dir,
                    review_version=review_version,
                ),
            )

        return factory

    def _review_trigger(self) -> str:
        history = self.state.loopback.history
        if not history:
            return "initial"
        last = history[-1]
        return f"loopback:{last.category}:{last.cycle}"

    def _record_review(self, version: VersionDir, findings: list[Finding]) -> None:
        entry = next((v for v in self.state.review.versions if v.v == version.v), None)
        if entry is None:
            entry = ReviewVersion(v=version.v)
            self.state.review.versions.append(entry)
        entry.counts = loopback.counts(findings)
        entry.open_categories = loopback.open_categories(findings)
        entry.gate14_passed = self._gate14_passed(version)
        self._save()

    def _gate14_passed(self, version: VersionDir) -> bool | None:
        data = _read_envelope(version.path / "gate14.envelope.json")
        if data is None:
            return None
        answers = data.get("gate14") or []
        if not answers:
            return None
        return all(str(a.get("answer")).lower() == "yes" for a in answers)

    # -- formal corrections (own counter, never the category cap) -----------

    def _formal_pass(self, version: VersionDir) -> None:
        corrections = loopback.corrections_in(version.path)
        self.state.counters["pending_formal_corrections"] = len(corrections)
        if not corrections:
            return
        if self.state.loopback.formal_fix_passes >= self.settings.formal_fix_cap:
            log.warning(
                "formal-fix cap of %d reached; %d corrections left for the human",
                self.settings.formal_fix_cap,
                len(corrections),
            )
            return

        targets = {correction.target for correction in corrections}
        pass_no = self.state.loopback.formal_fix_passes + 1
        applied = False
        for target in ("claims", "description"):
            if target not in targets:
                continue
            applied |= self._formal_fix(target, corrections, pass_no, version)
        if not applied:
            return

        self.state.loopback.formal_fix_passes = pass_no
        self._save()
        # Rebuild the package from the corrected text, then re-run only the
        # three passes that produced the corrections.
        package.build_review_package(self.dossier, self.state, version.v)
        package_dir = version.path / "package"
        self._run_prompts(
            "REVIEW",
            version,
            FORMAL_RECHECK,
            self._review_ctx(package_dir, version.v),
            slot="recheck",
            output_ids={pid: f"{pid}r" for pid in FORMAL_RECHECK},
        )
        self.state.counters["pending_formal_corrections"] = len(
            loopback.corrections_in(version.path)
        )
        self._save()

    def _formal_fix(
        self,
        target: str,
        corrections: list[Correction],
        pass_no: int,
        review_version: VersionDir,
    ) -> bool:
        """One `x-formal-fix` call, addressed to whoever owns the target text.

        The prompt file names ip-spec; the corrections decide. A claims
        correction applied by the specification drafter would be an ownership
        violation, and the lane contract is what keeps the two from fighting.
        """
        spec = self.playbook.get("x-formal-fix")
        phase, filename, section, agent = (
            ("P5", "claims.md", "Final Claim Set", "ip-claims")
            if target == "claims"
            else ("P4", "description.md", "Description", "ip-spec")
        )
        version = self._open_version(phase, "formal-fix")
        ctx = AttemptContext(
            agent_override=agent,
            slot=f"{target}-{pass_no}",
            output_phase=phase,
            write_output=True,
            package_dir=review_version.path / "package",
            input_ctx=InputContext(
                phase=spec.phase,
                prompt_id=spec.id,
                package_dir=review_version.path / "package",
                review_version=review_version.v,
            ),
        )
        result = self.caller.execute(spec, version.v, ctx)
        corrected = section_body(result.deliverable, "Corrected Text")
        if not corrected:
            log.error("x-formal-fix returned no `### Corrected Text`; nothing applied")
            self._complete_version(phase, version, status="failed")
            return False
        self.dossier.write_file(version.path / filename, corrected + "\n", overwrite=True)
        record = self._version_record(phase, version)
        record.prompts_done.append(f"x-formal-fix:{target}")
        self._complete_version(phase, version)
        self.events.emit(
            "output_written",
            phase=phase,
            prompt="x-formal-fix",
            call_id=result.call_id,
            path=version.path / filename,
            corrections=len(corrections),
            section=section,
        )
        return True

    # -- routing ------------------------------------------------------------

    def _start_route(self, plan: loopback.RoutePlan, version: VersionDir) -> LoopbackEvent:
        cycle = max(
            (self.state.loopback.cycles.get(category, 0) for category in plan.categories),
            default=0,
        ) + 1
        for category in plan.categories:
            self.state.loopback.cycles[category] = (
                self.state.loopback.cycles.get(category, 0) + 1
            )
        event = LoopbackEvent(
            category=",".join(plan.categories),
            cycle=cycle,
            finding_ids=[fid for ids in plan.finding_ids.values() for fid in ids],
            route=list(plan.steps),
            done=[],
            review_version=version.v,
            gate2_reconfirm=plan.gate2_reconfirm,
            gate2_requested=False,
            narrowing_notes=plan.narrowing_notes,
        )
        self.state.loopback.history.append(event)
        self.events.emit(
            "loopback_routed",
            phase="REVIEW",
            categories=plan.categories,
            cycle=cycle,
            route=plan.steps,
            findings=event.finding_ids,
        )
        self._save()
        return event

    def _active_route(self) -> LoopbackEvent | None:
        for event in reversed(self.state.loopback.history):
            done = list(getattr(event, "done", []) or [])
            if len(done) < len(event.route):
                return event
            waiting = self.state.gate("2").status == "pending"
            if getattr(event, "gate2_requested", False) and waiting:
                return event
        return None

    def _run_route(self, event: LoopbackEvent) -> StopReason | None:
        """Run the combined loop-back pass, stopping at the Gate 2 delta if asked."""
        categories = [c for c in event.category.split(",") if c]
        trigger = f"loopback:{categories[0] if categories else 'review'}:{event.cycle}"
        done = list(getattr(event, "done", []) or [])

        for step in event.route:
            if step in done:
                if step == "x-loopback-p3":
                    reason = self._gate2_delta(event)
                    if reason is not None:
                        return reason
                continue
            phase = {"x-loopback-p3": "P3", "x-p4-revise": "P4", "x-p5-revise": "P5"}[step]
            version = self._open_version(phase, trigger)
            self._run_prompts(phase, version, (step,), self._route_ctx(event))
            self._complete_version(phase, version)
            done.append(step)
            event.done = done  # type: ignore[attr-defined]
            self._save()
            if step == "x-loopback-p3":
                reason = self._gate2_delta(event)
                if reason is not None:
                    return reason
        self.caller.cross_check()
        return None

    def _route_ctx(self, event: LoopbackEvent) -> Any:
        review_version = int(getattr(event, "review_version", 0)) or None

        def factory(spec: PromptSpec) -> AttemptContext:
            return AttemptContext(
                input_ctx=InputContext(
                    phase=spec.phase,
                    prompt_id=spec.id,
                    review_version=review_version,
                )
            )

        return factory

    def _gate2_delta(self, event: LoopbackEvent) -> StopReason | None:
        """Re-confirm the new candidate embodiments before they can be drafted.

        A loop-back into Phase 3 produces new candidates, and Phase 4 may only
        draft from confirmed material. Skipping this would let the reviewer's
        findings put unconfirmed matter into the specification.
        """
        if not getattr(event, "gate2_reconfirm", False):
            return None
        gate_state = self.state.gate("2")
        if not getattr(event, "gate2_requested", False):
            payload, blocks = self._gate_payload("2")
            payload["new_candidates"] = self._phase_files("P3")
            payload["triggered_by"] = event.finding_ids
            self.gates.request("2", payload, blocks)
            event.gate2_requested = True  # type: ignore[attr-defined]
            self._save()
            return StopReason.GATE
        if gate_state.status == "pending":
            return StopReason.GATE
        if gate_state.decision == "reject":
            self.state.status = "failed"
            return StopReason.FAILED
        return None

    def _cap_gate_args(self, category: str) -> tuple[str, dict[str, Any], list[str]]:
        gate = f"loopback-cap-{category}"
        payload, blocks = self._gate_payload(gate)
        return gate, payload, blocks

    def _apply_forced_decisions(self) -> StopReason | None:
        """Consume cap-gate decisions the human has taken since the last run."""
        for decision in self.state.loopback.forced_decisions:
            if getattr(decision, "applied", False):
                continue
            if decision.decision == "abort":
                self.state.status = "failed"
                return StopReason.FAILED
            if decision.decision == "narrow":
                # One authorised narrowing, then straight back to review.
                version = self._open_version("P5", f"cap-narrow:{decision.category}")
                self._run_prompts("P5", version, ("x-p5-revise",), self._inner_ctx)
                self._complete_version("P5", version)
            decision.applied = True  # type: ignore[attr-defined]
            self._close_running("REVIEW")
            self._save()
        return None

    # -- packaging ----------------------------------------------------------

    def _package_stage(self) -> StopReason | None:
        package_state = self.state.phase("PACKAGE")
        if package_state.status == "complete" and self._force_from != "package":
            return None
        self._force_from = None

        review = self._newest_review()
        if review is None:
            raise RuntimeError("cannot package: no review version exists")
        package_dir = review.path / "package"

        version = self._open_version("PACKAGE", "initial")
        self.events.emit("phase_started", phase="PACKAGE", prompts=["39a", *CLEAN_PROMPTS])

        # The abstract is written last, from finished text only, and lands in
        # the reviewed package so 39/gate14/40 see the same bytes counsel will.
        self._run_prompts(
            "PACKAGE",
            version,
            ("39a",),
            self._review_ctx(package_dir, review.v),
        )
        self._run_prompts(
            "REVIEW",
            review,
            CLEAN_PROMPTS,
            self._review_ctx(package_dir, review.v),
        )
        findings = loopback.aggregate(review.path)
        self._record_review(review, findings)
        self._complete_version("REVIEW", review)

        target = package.build_final_package(self.dossier, self.state, version.v)
        self.events.emit("package_built", phase="PACKAGE", path=target, kind="final")

        memo = self._cover_memo(package_dir, review.v, version.v)
        if memo:
            package.write_cover_memo(self.dossier, target, memo)
        self._complete_version("PACKAGE", version)
        self._save()
        return None

    def _cover_memo(self, package_dir: Path, review_version: int, version: int) -> str:
        """Prose from ip-orchestrator. A failure here degrades, it does not block."""
        spec = self.playbook.get("x-package-memo")
        ctx = AttemptContext(
            write_output=False,
            allow_truncate=True,
            package_dir=package_dir,
            input_ctx=InputContext(
                phase=spec.phase,
                prompt_id=spec.id,
                package_dir=package_dir,
                review_version=review_version,
            ),
        )
        try:
            result = self.caller.execute(spec, version, ctx)
        except Exception as exc:  # deliberately broad: the fallback memo is honest
            log.warning("cover memo failed (%s); keeping the runner's factual fallback", exc)
            return ""
        self._save()
        return result.reply_text

    # -- dry run ------------------------------------------------------------

    def _dry_run(self, from_phase: str | None) -> DryRunReport:
        """Assemble the next stage's prompts and report, without calling anything."""
        stage, prompts, phase, version = self._next_work(from_phase)
        report = DryRunReport(stage=stage)
        for prompt_id in prompts:
            spec = self.playbook.get(prompt_id)
            ctx = AttemptContext(
                dry_run=True,
                allow_truncate=True,
                input_ctx=InputContext(phase=spec.phase, prompt_id=spec.id),
            )
            if stage.startswith("gate"):
                ctx.slot = f"gate-{stage.removeprefix('gate')}"
                ctx.extra_inputs = [
                    synthetic_input(
                        spec=f"gate:{stage}",
                        label=f"gates/gate-{stage.removeprefix('gate')}.pending.json",
                        text="{}",
                    )
                ]
            result = self.caller.execute(spec, version, ctx)
            assembled = result.assembled
            report.rows.append(
                DryRunRow(
                    prompt=prompt_id,
                    chars=assembled.chars,
                    transport=assembled.transport_hint,
                    inputs=[item.spec for item in assembled.inputs],
                    missing=[*assembled.missing_required, *assembled.missing_optional],
                    prompt_path=self.dossier.runs_dir / result.call_id / "prompt.md",
                )
            )
        _ = phase
        return report

    def _next_work(self, from_phase: str | None) -> tuple[str, tuple[str, ...], str, int]:
        """(stage, prompts, phase, version) for whatever would run next."""
        target = FROM_STAGE[from_phase] if from_phase else None
        for stage in STAGES:
            if target is not None and stage != target:
                continue
            if stage == "P1" and self._incomplete("P1"):
                return stage, P1_PROMPTS, "P1", self._next_version("P1")
            if stage == "gate1" and self.state.gate("1").status != "decided":
                return stage, ("x-gate-brief",), "PACKAGE", 1
            if stage == "P2" and self._incomplete("P2"):
                return stage, P2_PROMPTS, "P2", self._next_version("P2")
            if stage == "P3" and self._incomplete("P3"):
                return stage, P3_PROMPTS, "P3", self._next_version("P3")
            if stage == "gate2" and self.state.gate("2").status != "decided":
                return stage, ("x-gate-brief",), "PACKAGE", 1
            if stage == "inner" and not self.state.inner_loop.converged:
                round_no = self.state.inner_loop.round + 1
                prompts = P4_ROUND1 if round_no == 1 else P4_REVISE
                return stage, prompts, "P4", self._next_version("P4")
            if stage == "review":
                return stage, REVIEW_PROMPTS, "REVIEW", self._next_version("REVIEW")
            if stage == "package":
                return stage, ("39a", *CLEAN_PROMPTS), "PACKAGE", self._next_version("PACKAGE")
        return "done", (), "PACKAGE", 1

    def _incomplete(self, phase: str) -> bool:
        return self.state.phase(phase).status != "complete"

    def _next_version(self, phase: str) -> int:
        existing = self.dossier.versions(phase)
        running = [vd for vd in existing if vd.read_version_json().get("status") == "running"]
        if running:
            return max(vd.v for vd in running)
        return max((vd.v for vd in existing), default=0) + 1

    # -- versions and state -------------------------------------------------

    def _open_version(self, phase: str, trigger: str) -> VersionDir:
        """Reuse the running version of a phase, or open the next one."""
        for version_dir in reversed(self.dossier.versions(phase)):
            if version_dir.read_version_json().get("status") == "running":
                return version_dir
        version_dir = self.dossier.new_version(phase, trigger)
        phase_state = self.state.phase(phase)
        phase_state.status = "running"
        phase_state.current_version = version_dir.v
        phase_state.versions.append(
            VersionRecord(v=version_dir.v, dir=version_dir.name, trigger=trigger)
        )
        self._save()
        return version_dir

    def _complete_version(
        self, phase: str, version: VersionDir, status: str = "complete"
    ) -> None:
        self.dossier.complete_version(phase, version.v, status)
        record = self._version_record(phase, version)
        record.status = "complete" if status == "complete" else "failed"
        record.finished_at = _now()
        phase_state = self.state.phase(phase)
        phase_state.status = "complete" if status == "complete" else "failed"
        phase_state.current_version = version.v
        self._save()

    def _close_running(self, phase: str) -> None:
        """Finish a running version so the next entry opens a fresh one."""
        for version_dir in reversed(self.dossier.versions(phase)):
            if version_dir.read_version_json().get("status") == "running":
                self._complete_version(phase, version_dir)
                return

    def _version_record(self, phase: str, version: VersionDir) -> VersionRecord:
        phase_state = self.state.phase(phase)
        record = phase_state.version(version.v)
        if record is None:
            data = version.read_version_json()
            record = VersionRecord(
                v=version.v,
                dir=version.name,
                trigger=str(data.get("trigger", "unknown")),
                status="running",
            )
            phase_state.versions.append(record)
        return record

    def _newest_review(self) -> VersionDir | None:
        versions = self.dossier.versions("REVIEW")
        return versions[-1] if versions else None

    def _save(self) -> None:
        save_state(self.state, self.dossier.state_path)

    # -- payload helpers ----------------------------------------------------

    def _phase_files(self, phase: str) -> list[str]:
        version_dir = self.dossier.version(
            phase, self.state.phase(phase).current_version or 0
        ) or self.dossier.latest(phase)
        if version_dir is None:
            return []
        return [
            str(path.relative_to(self.dossier.root))
            for path in sorted(version_dir.path.glob("*.md"))
        ]

    def _review_files(self) -> list[str]:
        review = self._newest_review()
        if review is None:
            return []
        return [
            str(path.relative_to(self.dossier.root))
            for path in sorted(review.path.glob("*.md"))
        ] + [str((review.path / "findings.json").relative_to(self.dossier.root))]

    def _package_files(self) -> list[str]:
        version = self.dossier.latest("PACKAGE") or self._newest_review()
        if version is None:
            return []
        return [
            str(path.relative_to(self.dossier.root)) for path in sorted(version.path.glob("*"))
        ]

    def _open_questions(self, phase: str) -> list[str]:
        version_dir = self.dossier.latest(phase)
        if version_dir is None:
            return []
        out: list[str] = []
        for envelope_path in sorted(version_dir.path.glob("*.envelope.json")):
            data = _read_envelope(envelope_path)
            if data is None:
                continue
            for question in data.get("open_questions") or []:
                if isinstance(question, dict):
                    out.append(f"{question.get('id', '?')}: {question.get('text', '')}")
        return out

    def _open_finding_summaries(self, category: str) -> list[str]:
        review = self._newest_review()
        if review is None:
            return []
        return [
            f"{finding.id} ({finding.severity}): {finding.title} -> "
            f"{finding.least_narrowing_action}"
            for finding in loopback.load_findings(review.path)
            if finding.category == category and finding.severity in loopback.SEVERE
        ]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _output_exists(version_path: Path, prompt_id: str) -> bool:
    if (version_path / f"{prompt_id}.md").is_file():
        return True
    return any(version_path.glob(f"{prompt_id}-*.md"))


def _read_envelope(path: Path) -> dict[str, Any] | None:
    import json

    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("skipping unreadable envelope %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _pending_text(gate: str, record: dict[str, Any]) -> str:
    import json

    header = (
        f"Gate {gate} is pending. Options: "
        f"{', '.join(record.get('options', []))}. "
        f"It blocks: {', '.join(record.get('blocks', [])) or 'nothing recorded'}.\n\n"
    )
    return header + json.dumps(record, indent=2)


def _trigger(kind: str, round_no: int) -> str:
    return "initial" if round_no == 1 else f"{kind}:{round_no}"


def _describe(item: Any) -> str:
    if isinstance(item, dict):
        return " - ".join(str(value) for value in item.values() if value)
    return str(item)


def _last_error(exc: Exception, state: MatterState) -> Any:
    from ecosystem.state import LastError

    return LastError(
        kind=type(exc).__name__,
        message=str(exc)[:2000],
        phase=state.cursor.phase,
        prompt=state.cursor.step,
        call_id=state.cursor.call_id,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
