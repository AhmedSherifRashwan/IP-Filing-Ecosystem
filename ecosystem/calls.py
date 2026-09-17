"""One agent call, end to end: assemble, send, validate, correct once, write.

This is where the pipeline's two "one correction turn" rules live, and they are
deliberately the *same* shape:

* the reply does not satisfy the output contract -> one correction turn in the
  same session carrying the contract and the exact validation error;
* the Deliverable breaks the terminology lock -> one correction turn in the same
  session carrying the offending terms and the locked rows.

Same session, because the agent must see what it wrote; one turn, because a
model that has failed the same contract twice is not going to pass it on the
third attempt and the human needs to know rather than pay for a loop.

Everything a call touches on disk is under `runs/<call_id>/`: the prompt that
was sent, the reply as text, the raw RPC payload, and the metadata. A run that
dies mid-phase still explains itself from those four files.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ecosystem import numerals
from ecosystem.config import Settings
from ecosystem.contracts import (
    ContractError,
    DraftingEnvelope,
    ParsedReply,
    ReviewEnvelope,
    parse_reply,
)
from ecosystem.dossier import Dossier, InputContext, ResolvedInput, WouldOverwrite
from ecosystem.log import EventLog
from ecosystem.playbook import Playbook, PromptSpec
from ecosystem.prompt_builder import AssembledPrompt, assemble, contract_fragment_name
from ecosystem.state import AgentRun, MatterState
from ecosystem.terminology import LockedTable, TermReport, Violation, append_drift, check_output
from ecosystem.transport import (
    AutoTransport,
    CallRequest,
    GatewayReply,
    Transport,
    call_with_backoff,
    session_key,
)

log = logging.getLogger("ipfe")

__all__ = [
    "AttemptContext",
    "CallResult",
    "Caller",
    "ContractFailed",
    "TerminologyBlocked",
    "call_ids",
    "section_body",
]

# Agents whose Deliverable is filing text and is therefore policed against the
# terminology lock. ip-disclosure writes the table; ip-prior-art and ip-reviewer
# analyse, they do not draft.
_LOCKED_AGENTS: Final[frozenset[str]] = frozenset({"ip-embodiment", "ip-spec", "ip-claims"})
_LOCKED_KINDS: Final[frozenset[str]] = frozenset({"drafting", "compose", "revise", "formal-fix"})

_CORRECTION_TAIL: Final[str] = "Return the complete corrected reply, not a diff."

_SLUGIFY: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")
_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(```|~~~)")


class ContractFailed(RuntimeError):
    """The reply failed the output contract twice. Exit 4; both replies are on disk."""

    def __init__(self, call_id: str, message: str) -> None:
        super().__init__(f"{call_id}: output contract failed after one correction: {message}")
        self.call_id = call_id
        self.message = message


class TerminologyBlocked(RuntimeError):
    """The Deliverable still breaks the lock after one fix turn. Exit 5."""

    def __init__(self, call_id: str, violations: list[Violation], path: Path | None) -> None:
        listed = "; ".join(f"{v.term} ({v.rule}, locked row {v.row})" for v in violations)
        super().__init__(f"{call_id}: terminology violations remain: {listed}")
        self.call_id = call_id
        self.violations = violations
        self.path = path


# ---------------------------------------------------------------------------
# ids (plan s3.5)
# ---------------------------------------------------------------------------


def call_ids(
    slug: str,
    phase: str,
    prompt_id: str,
    version: int,
    attempt: int = 0,
    slot: str | None = None,
) -> tuple[str, str, str]:
    """`(call_id, session_suffix, idempotency_key)` for one attempt.

    The session suffix carries no attempt: a correction turn is the *same*
    session, so the agent can see what it sent. The call id and the idempotency
    key do carry it, because they identify the turn, and the gateway must not
    deduplicate a correction onto the reply being corrected.

    `slot` distinguishes repeats of the same prompt within one version - a gate
    brief, or the 37/38/40a recheck after a formal fix - which would otherwise
    collide on every id at once.
    """
    prompt_part = prompt_id.lower() if slot is None else f"{prompt_id.lower()}-{slot}"
    stem = f"{slug}-{phase.lower()}-{prompt_part}-v{version}"
    call_id = f"{stem}-a{attempt}"
    parts = [slug, phase, prompt_id, f"v{version}", f"a{attempt}"]
    if slot is not None:
        parts.insert(3, slot)
    return call_id, stem, ":".join(parts)


def slugify(text: str, limit: int = 48) -> str:
    """Filename-safe title for `<id>-<title_slug>.md`."""
    cleaned = _SLUGIFY.sub("-", (text or "output").lower()).strip("-")
    return (cleaned[:limit].rstrip("-")) or "output"


def section_body(markdown: str, title: str, level: int = 3) -> str:
    """Body of one H3 (or H<level>) section, ignoring headings inside code fences.

    Used for the write-backs that turn a reply section into a dossier file:
    `### Final Claim Set` -> `claims.md`, `### Description` -> `description.md`.
    Returns "" when the section is absent, which the caller must treat as a
    contract problem rather than as an empty claim set.
    """
    marker = re.compile(rf"^{'#' * level}\s+(.+?)\s*$")
    wanted = title.strip().lower()
    out: list[str] = []
    capturing = False
    fence: str | None = None
    for line in (markdown or "").splitlines():
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            token = fence_match.group(1)
            if fence is None:
                fence = token
            elif line.strip().startswith(fence):
                fence = None
        if fence is None:
            heading = marker.match(line)
            if heading:
                capturing = heading.group(1).strip().lower() == wanted
                continue
            # A heading at a shallower level ends the section.
            if capturing and re.match(rf"^#{{1,{level - 1}}}\s+", line):
                break
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


@dataclass
class AttemptContext:
    """Everything about one call that is not in the prompt file."""

    input_ctx: InputContext = field(default_factory=InputContext)
    # x-formal-fix is addressed to ip-claims or ip-spec depending on what the
    # corrections target; the prompt file cannot know which.
    agent_override: str | None = None
    extra_inputs: list[ResolvedInput] = field(default_factory=list)
    slot: str | None = None
    # `x-formal-fix` is a REVIEW prompt whose output belongs to the phase that
    # owns the corrected text. Without this its id and its output file would say
    # REVIEW while the version number meant P4/P5.
    output_phase: str | None = None
    output_prompt_id: str | None = None
    title_slug: str | None = None
    write_output: bool = True
    allow_truncate: bool = False
    dry_run: bool = False
    # Where `39a`'s abstract goes, and where `package:*` inputs are read from
    # while the review version is still being assembled.
    package_dir: Path | None = None


@dataclass
class CallResult:
    """What the runner needs after a call: the envelope, the file, the accounting."""

    call_id: str
    prompt_id: str
    phase: str
    agent_id: str
    session_key: str
    idempotency_key: str
    version: int
    assembled: AssembledPrompt
    slot: str | None = None
    reply_text: str = ""
    parsed: ParsedReply | None = None
    output_path: Path | None = None
    written_files: list[Path] = field(default_factory=list)
    transport: str = ""
    run_id: str | None = None
    provider: str | None = None
    model: str | None = None
    duration_ms: int = 0
    attempts: int = 1
    contract_corrected: bool = False
    terminology_corrected: bool = False
    terminology_warned: list[Violation] = field(default_factory=list)
    dry_run: bool = False

    @property
    def envelope(self) -> DraftingEnvelope | ReviewEnvelope | None:
        return self.parsed.envelope if self.parsed else None

    @property
    def deliverable(self) -> str:
        return self.parsed.deliverable if self.parsed else ""


# ---------------------------------------------------------------------------
# Caller
# ---------------------------------------------------------------------------


class Caller:
    """Executes prompt specs against a transport and writes the results down.

    Holds no per-call state; everything that varies is in `AttemptContext`, so
    the runner can interleave phases without the caller getting confused about
    which version it is writing into.
    """

    def __init__(
        self,
        dossier: Dossier,
        state: MatterState,
        playbook: Playbook,
        transport: Transport,
        settings: Settings,
        events: EventLog,
    ) -> None:
        self.dossier = dossier
        self.state = state
        self.playbook = playbook
        self.transport = transport
        self.settings = settings
        self.events = events
        # Everything but the transport wait runs under this lock, so several
        # prompts can be in flight at once while state/log/dossier writes stay
        # serialised. `_send` releases it for the duration of the gateway call.
        self.lock = threading.RLock()

    # -- public -------------------------------------------------------------

    def execute(
        self,
        spec: PromptSpec,
        version: int,
        attempt_ctx: AttemptContext | None = None,
    ) -> CallResult:
        """Run one prompt. Raises rather than returning a half-valid result."""
        with self.lock:
            return self._execute_locked(spec, version, attempt_ctx)

    def _execute_locked(
        self,
        spec: PromptSpec,
        version: int,
        attempt_ctx: AttemptContext | None = None,
    ) -> CallResult:
        ctx = attempt_ctx or AttemptContext()
        agent_id = ctx.agent_override or spec.agent
        phase = ctx.output_phase or spec.phase
        if ctx.package_dir is not None and ctx.input_ctx.package_dir is None:
            ctx.input_ctx.package_dir = ctx.package_dir

        call_id, suffix, idem = call_ids(self.dossier.slug, phase, spec.id, version, 0, ctx.slot)
        assembled = assemble(
            self.dossier,
            self.state,
            spec,
            ctx.input_ctx,
            self.settings,
            extra_inputs=ctx.extra_inputs,
            allow_truncate=ctx.allow_truncate,
            tolerate_missing=ctx.dry_run,
        )
        prompt_path = assembled.write(self.dossier.run_dir(call_id) / "prompt.md")

        result = CallResult(
            call_id=call_id,
            prompt_id=ctx.output_prompt_id or spec.id,
            phase=phase,
            agent_id=agent_id,
            session_key=session_key(agent_id, suffix),
            idempotency_key=idem,
            version=version,
            assembled=assembled,
            slot=ctx.slot,
            transport=assembled.transport_hint,
            dry_run=ctx.dry_run,
        )
        if ctx.dry_run:
            return result

        self.events.emit(
            "call_started",
            phase=spec.phase,
            prompt=spec.id,
            call_id=call_id,
            agent_id=agent_id,
            prompt_chars=assembled.chars,
            transport=assembled.transport_hint,
            dropped_optional=assembled.dropped_optional,
        )
        run = AgentRun(
            call_id=call_id,
            phase=spec.phase,
            prompt=spec.id,
            agent_id=agent_id,
            session_key=result.session_key,
            idempotency_key=idem,
            prompt_chars=assembled.chars,
        )
        self.state.agent_runs.append(run)
        self.state.cursor.call_id = call_id
        self.state.bump("calls")

        try:
            reply = self._send(result, prompt_path, assembled.text, spec.timeout_s)
        except Exception as exc:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = _now()
            self.events.emit(
                "call_failed",
                phase=spec.phase,
                prompt=spec.id,
                call_id=call_id,
                error=run.error,
            )
            raise

        result.reply_text = reply.text
        result.run_id = reply.run_id
        result.provider = reply.provider
        result.model = reply.model
        result.duration_ms = reply.duration_ms
        result.transport = reply.transport or assembled.transport_hint
        run.transport = result.transport
        run.run_id = reply.run_id
        run.provider = reply.provider
        run.model = reply.model
        run.duration_ms = reply.duration_ms
        run.reply_chars = len(reply.text)

        attempt = 0
        parsed, attempt = self._parse_with_correction(result, spec, reply, attempt)
        result.parsed = parsed

        report = self._terminology(result, spec, parsed)
        if report is not None and report.violations:
            parsed, attempt, report = self._terminology_correction(
                result, spec, parsed, report, attempt
            )
            result.parsed = parsed

        run.status = "ok"
        run.attempt = attempt
        run.finished_at = _now()
        self.events.emit(
            "call_finished",
            phase=spec.phase,
            prompt=spec.id,
            call_id=call_id,
            attempts=attempt + 1,
            duration_ms=reply.duration_ms,
            reply_chars=len(result.reply_text),
        )
        result.attempts = attempt + 1

        if ctx.write_output:
            self._write_output(result, spec, ctx)
        if report is not None:
            self._settle_terminology(result, spec, report)
        return result

    # -- transport ----------------------------------------------------------

    def _send(
        self, result: CallResult, prompt_path: Path, text: str, timeout_s: int
    ) -> GatewayReply:
        """Transport call with the caller lock released: this is the slow part."""
        self.lock.release()
        try:
            return self._send_inner(result, prompt_path, text, timeout_s)
        finally:
            self.lock.acquire()

    def _send_inner(
        self, result: CallResult, prompt_path: Path, text: str, timeout_s: int
    ) -> GatewayReply:
        req = CallRequest(
            agent_id=result.agent_id,
            prompt_path=prompt_path,
            prompt_text=text,
            session_key=result.session_key,
            idempotency_key=result.idempotency_key,
            timeout_s=timeout_s or self.settings.default_timeout_s,
        )
        # AutoTransport retries transient failures itself (and re-selects the
        # backend per attempt); wrapping it again would multiply the attempts.
        if isinstance(self.transport, AutoTransport):
            reply = self.transport.call(req)
        else:
            reply = call_with_backoff(self.transport, req)
        self._save_reply(result.call_id, reply, result)
        return reply

    def _save_reply(self, call_id: str, reply: GatewayReply, result: CallResult) -> None:
        run_dir = self.dossier.run_dir(call_id)
        (run_dir / "reply.md").write_text(reply.text, encoding="utf-8")
        (run_dir / "reply.raw.json").write_text(
            json.dumps(reply.raw, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (run_dir / "meta.json").write_text(
            json.dumps(
                {
                    "call_id": call_id,
                    "phase": result.phase,
                    "prompt": result.prompt_id,
                    "agent_id": result.agent_id,
                    "session_key": result.session_key,
                    "idempotency_key": result.idempotency_key,
                    "transport": reply.transport,
                    "run_id": reply.run_id,
                    "status": reply.status,
                    "provider": reply.provider,
                    "model": reply.model,
                    "duration_ms": reply.duration_ms,
                    "prompt_chars": result.assembled.chars,
                    "reply_chars": len(reply.text),
                    "started_at": _now(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # -- contract correction ------------------------------------------------

    def _parse_with_correction(
        self, result: CallResult, spec: PromptSpec, reply: GatewayReply, attempt: int
    ) -> tuple[ParsedReply, int]:
        try:
            return parse_reply(reply.text, spec.kind), attempt
        except ContractError as first:
            log.warning("contract failure on %s: %s", result.call_id, first.message)
            self.events.emit(
                "contract_corrected",
                phase=spec.phase,
                prompt=spec.id,
                call_id=result.call_id,
                error=first.message,
                missing=first.missing,
            )
            self.state.bump("contract_corrections")
            attempt += 1
            retry = self._correction_turn(
                result,
                spec,
                attempt,
                self._contract_correction_text(spec, first),
            )
            result.reply_text = retry.text
            result.contract_corrected = True
            try:
                return parse_reply(retry.text, spec.kind), attempt
            except ContractError as second:
                self.events.emit(
                    "contract_failed",
                    phase=spec.phase,
                    prompt=spec.id,
                    call_id=result.call_id,
                    error=second.message,
                )
                raise ContractFailed(result.call_id, second.message) from second

    def _contract_correction_text(self, spec: PromptSpec, error: ContractError) -> str:
        return "\n\n".join(
            [
                "## Output contract",
                self._contract_fragment(spec),
                "## What was wrong",
                error.message,
                _CORRECTION_TAIL,
            ]
        )

    def _contract_fragment(self, spec: PromptSpec) -> str:
        name = contract_fragment_name(spec.kind)
        return self.playbook.fragment(name).strip() if name else ""

    def _correction_turn(
        self, result: CallResult, spec: PromptSpec, attempt: int, message: str
    ) -> GatewayReply:
        """One more turn on the same session, with a new call id and idem key."""
        call_id, _, idem = call_ids(
            self.dossier.slug,
            result.phase,
            spec.id,
            result.version,
            attempt,
            result.slot,
        )
        run_dir = self.dossier.run_dir(call_id)
        (run_dir / "prompt.md").write_text(message, encoding="utf-8")
        req = CallRequest(
            agent_id=result.agent_id,
            prompt_path=run_dir / "prompt.md",
            prompt_text=message,
            session_key=result.session_key,
            idempotency_key=idem,
            timeout_s=spec.timeout_s or self.settings.default_timeout_s,
        )
        if isinstance(self.transport, AutoTransport):
            reply = self.transport.call(req)
        else:
            reply = call_with_backoff(self.transport, req)
        self._save_reply(call_id, reply, result)
        self.state.bump("retries")
        return reply

    # -- terminology --------------------------------------------------------

    def _locked_table(self) -> LockedTable | None:
        path = self.dossier.terminology_dir / "LOCKED.json"
        if not path.is_file():
            return None
        try:
            return LockedTable.from_json(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            log.error("LOCKED.json is unreadable (%s); terminology check skipped", exc)
            return None

    def _terminology(
        self, result: CallResult, spec: PromptSpec, parsed: ParsedReply
    ) -> TermReport | None:
        if spec.agent not in _LOCKED_AGENTS and result.agent_id not in _LOCKED_AGENTS:
            return None
        if spec.kind not in _LOCKED_KINDS:
            return None
        table = self._locked_table()
        if table is None:
            return None
        envelope = parsed.envelope if isinstance(parsed.envelope, DraftingEnvelope) else None
        return check_output(parsed.deliverable, envelope, table, spec.kind, spec.id)

    def _terminology_correction(
        self,
        result: CallResult,
        spec: PromptSpec,
        parsed: ParsedReply,
        report: TermReport,
        attempt: int,
    ) -> tuple[ParsedReply, int, TermReport]:
        table = self._locked_table()
        assert table is not None
        self.events.emit(
            "terminology_violation",
            phase=spec.phase,
            prompt=spec.id,
            call_id=result.call_id,
            violations=[v.model_dump() for v in report.violations],
        )
        self.state.bump("terminology_violations")
        attempt += 1
        retry = self._correction_turn(
            result, spec, attempt, self._terminology_correction_text(spec, report, table)
        )
        result.terminology_corrected = True
        try:
            reparsed = parse_reply(retry.text, spec.kind)
        except ContractError as exc:
            # A terminology fix that breaks the contract is a contract failure:
            # the reply is unusable either way, and the human gets both errors.
            raise ContractFailed(result.call_id, exc.message) from exc
        result.reply_text = retry.text
        envelope = reparsed.envelope if isinstance(reparsed.envelope, DraftingEnvelope) else None
        again = check_output(reparsed.deliverable, envelope, table, spec.kind, spec.id)
        return reparsed, attempt, again

    def _terminology_correction_text(
        self, spec: PromptSpec, report: TermReport, table: LockedTable
    ) -> str:
        offending = "\n".join(
            f"- `{v.term}` ({v.rule}) in: ...{v.context}..." for v in report.violations
        )
        wanted = {v.row for v in report.violations}
        rows = "\n".join(
            f"- preferred: **{row.preferred}** | synonyms: {', '.join(row.synonyms) or '-'} "
            f"| avoid: {', '.join(row.avoid) or '-'}"
            for row in table.rows
            if row.preferred in wanted
        )
        return "\n\n".join(
            [
                "## Terminology violations",
                "Your `## Deliverable` breaks the locked terminology for this matter:",
                offending,
                "## The locked rows these belong to",
                rows or "(see the locked terminology table in the task)",
                "Replace each offending term with the locked preferred term, or - if the "
                "term really is a new concept - declare it in `new_terms` with the term it "
                "maps to and a reason. Do not change anything else.",
                "## Output contract",
                self._contract_fragment(spec),
                _CORRECTION_TAIL,
            ]
        )

    def _settle_terminology(self, result: CallResult, spec: PromptSpec, report: TermReport) -> None:
        """Record drift, then block or warn on anything still violating."""
        if report.drift:
            count = append_drift(self.dossier.drift_path, report.drift)
            self.state.terminology.drift_logged += count
            self.events.emit(
                "terminology_drift",
                phase=spec.phase,
                prompt=spec.id,
                call_id=result.call_id,
                terms=[d.term for d in report.drift],
            )
        result.terminology_warned = list(report.warnings)
        if not report.violations:
            return
        # The lock is binding where text becomes the specification or the
        # claims. Earlier phases name competitors' variants and rejected
        # synonyms as part of their job (a design-around *is* the avoided
        # term), so there a violation is drift to log, not a reason to stop.
        block_phases = tuple(
            getattr(self.settings, "terminology_block_phases", ("P4", "P5", "REVIEW"))
        )
        policy = self.settings.terminology_policy
        if policy == "block" and spec.phase not in block_phases:
            policy = "warn"
        if policy == "warn":
            log.warning(
                "terminology violations kept (policy warn, phase %s) on %s: %s",
                spec.phase,
                result.call_id,
                ", ".join(v.term for v in report.violations),
            )
            self.events.emit(
                "terminology_violation",
                phase=spec.phase,
                prompt=spec.id,
                call_id=result.call_id,
                policy="warn",
                violations=[v.model_dump() for v in report.violations],
            )
            return
        self.state.terminology.violations_blocked += len(report.violations)
        if result.output_path is not None:
            self.dossier.mark_status(result.output_path, "rejected")
        self.events.emit(
            "terminology_violation",
            phase=spec.phase,
            prompt=spec.id,
            call_id=result.call_id,
            policy="block",
            violations=[v.model_dump() for v in report.violations],
        )
        raise TerminologyBlocked(result.call_id, report.violations, result.output_path)

    # -- writing ------------------------------------------------------------

    def _write_output(self, result: CallResult, spec: PromptSpec, ctx: AttemptContext) -> None:
        assert result.parsed is not None
        frontmatter: dict[str, Any] = {
            "matter": self.dossier.slug,
            "phase": result.phase,
            "prompt": result.prompt_id,
            "version": result.version,
            "agent": result.agent_id,
            "model": result.model,
            "provider": result.provider,
            "run_id": result.run_id,
            "call_id": result.call_id,
            "created_at": _now(),
            "status": "candidate",
            "inputs": result.assembled.input_refs(self.dossier.root),
        }
        title_slug = ctx.title_slug or slugify(spec.title)
        result.output_path = self.dossier.write_output(
            result.phase,
            result.version,
            result.prompt_id,
            title_slug,
            result.parsed.body_md,
            frontmatter,
            envelope=result.parsed.envelope_raw,
        )
        self.state.bump("outputs")
        self.events.emit(
            "output_written",
            phase=result.phase,
            prompt=result.prompt_id,
            call_id=result.call_id,
            path=result.output_path,
        )
        result.written_files.extend(self.write_backs(result, spec, ctx))

    # -- write-backs (plan s3.7) -------------------------------------------

    def write_backs(self, result: CallResult, spec: PromptSpec, ctx: AttemptContext) -> list[Path]:
        """Project a reply section onto the file the rest of the pipeline reads.

        These are the only places where an agent's prose becomes a first-class
        dossier artefact rather than one more numbered output.
        """
        written: list[Path] = []
        deliverable = result.deliverable
        if spec.id == "20a":
            written.extend(self._write_registry(result, deliverable))
        elif spec.id in {"25", "x-p5-revise"}:
            written.append(
                self._write_derived(
                    "P5", result.version, "claims.md", deliverable, "Final Claim Set", spec
                )
            )
        elif spec.id in {"x-p4-compose-description", "x-p4-revise"}:
            written.append(
                self._write_derived(
                    "P4", result.version, "description.md", deliverable, "Description", spec
                )
            )
        elif spec.id == "39a":
            package_dir = ctx.package_dir or ctx.input_ctx.package_dir
            if package_dir is None:
                raise WouldOverwrite("39a needs a review package directory to write abstract.md")
            body = section_body(deliverable, "Abstract") or deliverable
            written.append(
                self.dossier.write_file(package_dir / "abstract.md", body + "\n", overwrite=True)
            )
        return [path for path in written if path is not None]

    def _write_derived(
        self,
        phase: str,
        version: int,
        filename: str,
        deliverable: str,
        section: str,
        spec: PromptSpec,
    ) -> Path:
        body = section_body(deliverable, section)
        if not body:
            raise ContractFailed(
                f"{spec.id}", f"the `### {section}` section is missing from the Deliverable"
            )
        version_dir = self.dossier.version(phase, version)
        if version_dir is None:
            raise WouldOverwrite(f"no version v{version} of {phase} to write {filename} into")
        status = version_dir.read_version_json().get("status")
        if status != "running":
            raise WouldOverwrite(
                f"{version_dir.path / filename} belongs to a {status} version; "
                "open a new version instead of rewriting a finished one"
            )
        # Within a *running* version a later prompt legitimately supersedes an
        # earlier one (x-p5-revise then 25). Once the version is completed the
        # file is frozen, which is what the check above enforces.
        return self.dossier.write_file(version_dir.path / filename, body + "\n", overwrite=True)

    def _write_registry(self, result: CallResult, deliverable: str) -> list[Path]:
        table = section_body(deliverable, "Reference-Numeral Registry") or deliverable
        try:
            # `source` is the prompt id, not the path: registry.json is a package
            # member and the blind reviewer must not be told which drafting
            # output produced it.
            registry = numerals.parse_registry(table, source="20a")
        except ValueError as exc:
            raise ContractFailed(result.call_id, f"prompt 20a: {exc}") from exc
        written = [
            self.dossier.write_file(
                self.dossier.registry_path, registry.to_json() + "\n", overwrite=True
            )
        ]
        crosscheck = self.cross_check()
        if crosscheck is not None:
            written.append(crosscheck)
        return written

    def cross_check(self) -> Path | None:
        """Refresh `numerals/cross-check.json` when both halves of the text exist.

        Deterministic and never auto-fixed: the result is an input to Prompt 38,
        which is where a human-readable judgement is made (plan s3.10).
        """
        if not self.dossier.registry_path.is_file():
            return None
        description = self._current_file("P4", "description.md")
        claims = self._current_file("P5", "claims.md")
        if description is None or claims is None:
            return None
        registry = numerals.Registry.from_json(
            self.dossier.registry_path.read_text(encoding="utf-8")
        )
        result = numerals.cross_check(registry, description, claims)
        return self.dossier.write_file(
            self.dossier.crosscheck_path, result.to_json() + "\n", overwrite=True
        )

    def _current_file(self, phase: str, filename: str) -> str | None:
        """Read a derived file from the running version if there is one, else the latest."""
        for version_dir in reversed(self.dossier.versions(phase)):
            candidate = version_dir.path / filename
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
