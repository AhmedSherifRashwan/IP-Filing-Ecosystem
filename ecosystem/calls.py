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
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from ecosystem import numerals
from ecosystem.config import CRITICAL_PROMPTS, REVISION_PROMPTS, Settings
from ecosystem.contracts import (
    ContractError,
    DraftingEnvelope,
    ParsedReply,
    ReviewEnvelope,
    parse_reply,
    reply_looks_defective,
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
    GatewayError,
    GatewayRateLimited,
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
    requested_model: str | None = None
    prefer_gateway: bool = False
    skipped: bool = False
    partial: bool = False
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


_RESET_RE = re.compile(r"resets?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.IGNORECASE)


def _seconds_until_reset(message: str, now: datetime | None = None) -> float | None:
    """Seconds until the local reset time a provider message names, plus a margin.

    "You've hit your session limit · resets 3:20pm" is the Claude CLI wording;
    the time is in the machine's local zone. Unparseable → None (poll instead).
    """
    match = _RESET_RE.search(message)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    minute = int(match.group(2) or 0)
    if match.group(3).lower() == "pm":
        hour += 12
    current = now or datetime.now().astimezone()
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return (target - current).total_seconds() + 90.0


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
        # Model tiering: analysis prompts run on the analysis-tier model; a
        # `model:` line in the prompt's frontmatter wins over the tier.
        explicit = getattr(spec, "model", None) or (spec.model_extra or {}).get("model")
        result.requested_model = self.settings.model_for_prompt(spec.id, explicit)
        if result.requested_model:
            result.transport = "agent-cli"
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
            requested_model=result.requested_model,
        )
        self.state.agent_runs.append(run)
        self.state.cursor.call_id = call_id
        self.state.bump("calls")

        try:
            reply = self._send_with_fallbacks(result, spec, prompt_path, assembled.text)
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
            # A provider that would not answer is not a reason to lose a run.
            # The same ladder that handles an unusable reply handles an absent
            # one: skip a revision, record a non-critical step as failed and
            # carry on, stop only for a step nothing can replace.
            settled = self._fallback(result, spec, ctx, run, exc)
            if settled is None:
                raise
            return settled

        reply = self._retry_defective(result, spec, prompt_path, assembled.text, reply)
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
        try:
            parsed, attempt = self._parse_with_correction(result, spec, reply, attempt)
        except ContractFailed as exc:
            fallback = self._fallback(result, spec, ctx, run, exc)
            if fallback is None:
                raise
            return fallback
        result.parsed = parsed

        report = self._terminology(result, spec, parsed)
        if report is not None and report.violations and self._terminology_would_block(spec):
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

    # -- resilience ---------------------------------------------------------

    def _fresh_session(self, result: CallResult, tag: str) -> None:
        """Point the result at a new session and idempotency key for a re-run.

        The gateway holds one turn per key and a fresh session has no memory
        of a defective attempt. The gateway path is preferred when the prompt
        fits: it has never returned a partial answer, the CLI path has.
        """
        base_key = result.session_key.split("-fresh")[0]
        base_idem = result.idempotency_key.split(":fresh")[0]
        result.session_key = f"{base_key}-{tag}"
        result.idempotency_key = f"{base_idem}:{tag}"
        result.prefer_gateway = True

    def _send_with_fallbacks(
        self, result: CallResult, spec: PromptSpec, prompt_path: Path, text: str
    ) -> GatewayReply:
        """Send, and if the analysis tier will not answer, use the agent's own model.

        Model tiering is an economy, not a requirement: when the second provider
        is unreachable the step is still worth doing on the first one. Falling
        back costs tokens and says so in the ticker; failing would cost the run.
        """
        try:
            return self._send(result, prompt_path, text, spec.timeout_s)
        except GatewayError as exc:
            if not result.requested_model:
                raise
            log.warning(
                "tier model %s unavailable for %s (%s); falling back to %s's own model",
                result.requested_model,
                result.call_id,
                exc,
                result.agent_id,
            )
            self.events.emit(
                "tier_fallback",
                phase=spec.phase,
                prompt=spec.id,
                call_id=result.call_id,
                requested_model=result.requested_model,
                error=str(exc)[:200],
            )
            result.requested_model = None
            result.transport = ""
            self._fresh_session(result, "tier")
            return self._send(result, prompt_path, text, spec.timeout_s)

    def _retry_defective(
        self,
        result: CallResult,
        spec: PromptSpec,
        prompt_path: Path,
        text: str,
        reply: GatewayReply,
    ) -> GatewayReply:
        """Re-run an empty or head-truncated reply in a fresh session, up to `fresh_retries`."""
        retries = int(getattr(self.settings, "fresh_retries", 2))
        n = 0
        while n < retries:
            reason = reply_looks_defective(reply.text, spec.kind)
            if reason is None:
                return reply
            n += 1
            self.events.emit(
                "reply_defective",
                phase=spec.phase,
                prompt=spec.id,
                call_id=result.call_id,
                reason=reason,
                retry=n,
                reply_chars=len(reply.text),
            )
            keep = (result.session_key, result.idempotency_key, result.prefer_gateway)
            self._fresh_session(result, f"fresh{n}")
            try:
                reply = self._send(result, prompt_path, text, spec.timeout_s)
            except Exception as exc:  # keep what we have; the contract ladder decides
                log.warning("fresh retry %d of %s failed: %s", n, result.call_id, exc)
                result.session_key, result.idempotency_key, result.prefer_gateway = keep
                break
        return reply

    def _fallback(
        self,
        result: CallResult,
        spec: PromptSpec,
        ctx: AttemptContext,
        run: AgentRun,
        error: Exception,
    ) -> CallResult | None:
        """What happens when a reply never follows the contract.

        A revision prompt is skipped: the previous text stands and the reviewer
        sees it. A non-critical prompt keeps its raw reply as a partial output.
        A critical prompt (the disclosure, the terminology, the description, the
        claims, the abstract) has no substitute, so the run stops. None = stop.
        """
        critical = spec.id in getattr(self.settings, "critical_prompts", CRITICAL_PROMPTS)
        if critical:
            return None
        message = getattr(error, "message", None) or str(error)
        if not result.reply_text:
            # Nothing came back at all, so there is nothing to keep. The step is
            # recorded as failed -- which advances the bar and shows in the
            # ticker -- and the run goes on without it.
            run.status = "failed"
            run.error = f"{type(error).__name__}: {message}"
            run.finished_at = _now()
            result.skipped = True
            self.events.emit(
                "revision_skipped" if spec.id in REVISION_PROMPTS else "output_partial",
                phase=spec.phase,
                prompt=spec.id,
                call_id=result.call_id,
                error=message,
            )
            self.state.bump("steps_lost")
            return result
        run.finished_at = _now()
        result.attempts = max(result.attempts, 1)
        if spec.id in REVISION_PROMPTS:
            run.status = "skipped"
            result.skipped = True
            self.events.emit(
                "revision_skipped",
                phase=spec.phase,
                prompt=spec.id,
                call_id=result.call_id,
                error=message,
            )
            self.state.bump("revisions_skipped")
            return result
        run.status = "partial"
        result.partial = True
        if ctx.write_output:
            self._write_partial(result, spec, ctx, error)
        self.events.emit(
            "output_partial",
            phase=spec.phase,
            prompt=spec.id,
            call_id=result.call_id,
            error=message,
        )
        self.state.bump("outputs_partial")
        return result

    def _write_partial(
        self, result: CallResult, spec: PromptSpec, ctx: AttemptContext, error: Exception
    ) -> None:
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
            "status": "partial",
            "inputs": result.assembled.input_refs(self.dossier.root),
        }
        envelope = {
            "prompt": spec.id,
            "status": "partial",
            "assumptions": [],
            "open_questions": [
                {
                    "id": "Q-RUNNER",
                    "text": "This reply did not follow the output contract after a "
                    f"correction turn and a fresh attempt ({error}). The raw reply "
                    "is kept verbatim; treat every statement in it as unverified.",
                    "blocks": [],
                }
            ],
            "new_terms": [],
            "support_refs": [],
        }
        result.output_path = self.dossier.write_output(
            result.phase,
            result.version,
            result.prompt_id,
            ctx.title_slug or slugify(spec.title),
            result.reply_text or "",
            frontmatter,
            envelope=envelope,
        )
        self.state.bump("outputs")

    # -- transport ----------------------------------------------------------

    def _send(
        self, result: CallResult, prompt_path: Path, text: str, timeout_s: int
    ) -> GatewayReply:
        """Transport call with the caller lock released: this is the slow part.

        A provider quota or session limit is not a failure of the matter; an
        unattended run that dies on it strands hours of work. So the call waits
        (polling, or until the reset time the message names) and retries, for
        up to `quota_wait_max_s`, telling the human through the event log.
        """
        self.lock.release()
        try:
            waited = 0.0
            while True:
                try:
                    return self._send_inner(result, prompt_path, text, timeout_s)
                except GatewayRateLimited as exc:
                    max_wait = float(getattr(self.settings, "quota_wait_max_s", 6 * 3600))
                    poll = float(getattr(self.settings, "quota_poll_s", 600))
                    if waited >= max_wait:
                        raise
                    delay = min(_seconds_until_reset(str(exc)) or poll, max_wait - waited)
                    self.events.emit(
                        "quota_wait",
                        phase=result.phase,
                        prompt=result.prompt_id,
                        call_id=result.call_id,
                        error=str(exc),
                        wait_s=int(delay),
                        waited_s=int(waited),
                    )
                    self._sleep(delay)
                    waited += delay
                    self.events.emit(
                        "quota_resumed",
                        phase=result.phase,
                        prompt=result.prompt_id,
                        call_id=result.call_id,
                        waited_s=int(waited),
                    )
        finally:
            self.lock.acquire()

    # Injectable so tests never sleep.
    _sleep = staticmethod(time.sleep)

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
            model=result.requested_model,
            prefer_gateway=result.prefer_gateway,
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

    # The reply section each write-back projects onto a dossier file. A reply
    # that parses but lacks it is unusable for the pipeline and goes through
    # the same correction ladder as a reply that does not parse at all.
    WRITEBACK_SECTIONS: dict[str, str] = {
        "20a": "Reference-Numeral Registry",
        "25": "Final Claim Set",
        "x-p5-revise": "Final Claim Set",
        "x-p4-compose-description": "Description",
        "x-p4-revise": "Description",
        "x-formal-fix": "Corrected Text",
    }

    def _parse_checked(self, text: str, spec: PromptSpec) -> ParsedReply:
        parsed = parse_reply(text, spec.kind)
        wanted = self.WRITEBACK_SECTIONS.get(spec.id)
        if wanted:
            deliverable = parsed.sections.get("Deliverable", "")
            pattern = rf"^#{{2,4}}\s*{re.escape(wanted)}\s*:?\s*$"
            if not re.search(pattern, deliverable, re.IGNORECASE | re.MULTILINE):
                raise ContractError(
                    f"the `### {wanted}` section is missing from the Deliverable",
                    missing=[f"### {wanted}"],
                )
        return parsed

    def _parse_with_correction(
        self, result: CallResult, spec: PromptSpec, reply: GatewayReply, attempt: int
    ) -> tuple[ParsedReply, int]:
        try:
            return self._parse_checked(reply.text, spec), attempt
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
            try:
                retry = self._correction_turn(
                    result,
                    spec,
                    attempt,
                    self._contract_correction_text(
                        spec, first, result.assembled.text, result.reply_text or ""
                    ),
                )
            except Exception as exc:  # a failed correction call is a failed correction
                log.warning("correction turn for %s failed: %s", result.call_id, exc)
                fresh = self._fresh_rerun(result, spec, attempt)
                if fresh is not None:
                    return fresh, attempt + 1
                raise ContractFailed(result.call_id, first.message) from exc
            result.reply_text = retry.text
            result.contract_corrected = True
            try:
                return self._parse_checked(retry.text, spec), attempt
            except ContractError as second:
                fresh = self._fresh_rerun(result, spec, attempt)
                if fresh is not None:
                    return fresh, attempt + 1
                self.events.emit(
                    "contract_failed",
                    critical=spec.id
                    in getattr(self.settings, "critical_prompts", CRITICAL_PROMPTS),
                    phase=spec.phase,
                    prompt=spec.id,
                    call_id=result.call_id,
                    error=second.message,
                )
                raise ContractFailed(result.call_id, second.message) from second

    def _fresh_rerun(
        self, result: CallResult, spec: PromptSpec, attempt: int
    ) -> ParsedReply | None:
        """One more full attempt in a clean session before the fallback ladder."""
        self._fresh_session(result, f"rerun{attempt}")
        prompt_path = self.dossier.run_dir(result.call_id) / "prompt.md"
        try:
            reply = self._send(result, prompt_path, result.assembled.text, spec.timeout_s)
        except Exception as exc:  # the ladder below decides; a failed re-run is not fatal
            log.warning("fresh re-run of %s failed: %s", result.call_id, exc)
            return None
        reply = self._retry_defective(result, spec, prompt_path, result.assembled.text, reply)
        try:
            parsed = self._parse_checked(reply.text, spec)
        except ContractError as exc:
            log.warning("fresh re-run of %s still off-contract: %s", result.call_id, exc.message)
            return None
        result.reply_text = reply.text
        self.events.emit(
            "contract_corrected",
            phase=spec.phase,
            prompt=spec.id,
            call_id=result.call_id,
            error="recovered by a fresh-session re-run",
        )
        return parsed

    def _contract_correction_text(
        self,
        spec: PromptSpec,
        error: ContractError,
        original_prompt: str = "",
        previous_reply: str = "",
    ) -> str:
        """A correction turn that stands on its own.

        Not every runtime carries the session forward (a tiered call through
        `openclaw agent --model` starts with no memory of the task), so the
        turn restates the task and the reply being corrected instead of
        assuming the model still has them.
        """
        parts = ["## Output contract", self._contract_fragment(spec)]
        if original_prompt:
            parts += ["## The task you were given", original_prompt]
        if previous_reply:
            parts += ["## Your previous reply (to be corrected)", previous_reply]
        parts += ["## What was wrong", error.message, _CORRECTION_TAIL]
        return "\n\n".join(parts)

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
            result,
            spec,
            attempt,
            self._terminology_correction_text(
                spec, report, table, result.assembled.text, result.reply_text or ""
            ),
        )
        result.terminology_corrected = True
        try:
            reparsed = parse_reply(retry.text, spec.kind)
        except ContractError as exc:
            # The original reply was valid; a fix turn that comes back broken
            # must not throw it away. Keep the original and let the settle
            # step decide what its remaining violations mean.
            log.warning(
                "terminology fix turn on %s broke the contract (%s); keeping the original reply",
                result.call_id,
                exc.message,
            )
            self.events.emit(
                "contract_corrected",
                phase=spec.phase,
                prompt=spec.id,
                call_id=result.call_id,
                error=f"terminology fix turn discarded: {exc.message}",
            )
            return parsed, attempt, report
        result.reply_text = retry.text
        envelope = reparsed.envelope if isinstance(reparsed.envelope, DraftingEnvelope) else None
        again = check_output(reparsed.deliverable, envelope, table, spec.kind, spec.id)
        return reparsed, attempt, again

    def _terminology_correction_text(
        self,
        spec: PromptSpec,
        report: TermReport,
        table: LockedTable,
        original_prompt: str = "",
        previous_reply: str = "",
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
                *(["## The task you were given", original_prompt] if original_prompt else []),
                *(
                    ["## Your previous reply (to be corrected)", previous_reply]
                    if previous_reply
                    else []
                ),
                _CORRECTION_TAIL,
            ]
        )

    def _terminology_would_block(self, spec: PromptSpec) -> bool:
        """Whether a remaining violation stops the run for this prompt.

        Only then is a fix turn worth a call; elsewhere the violation is drift
        for the reviewer's consistency audit. Mirrors `_settle_terminology`.
        """
        if self.settings.terminology_policy != "block":
            return False
        if getattr(self.state, "unattended", False):
            return False
        block_phases = tuple(
            getattr(self.settings, "terminology_block_phases", ("P4", "P5", "REVIEW"))
        )
        return spec.phase in block_phases

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
        # An unattended run has nobody to fix a stuck synonym; stopping would
        # only strand the matter. The violation is logged as drift and the
        # reviewer's consistency audit (Prompt 38) reports it as a correction.
        if policy == "block" and getattr(self.state, "unattended", False):
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
        if result.parsed.warnings:
            # What the parser repaired: a record for the evidence pack that the
            # reply was read tolerantly, not a defect of the content.
            frontmatter["contract_warnings"] = list(result.parsed.warnings)
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
