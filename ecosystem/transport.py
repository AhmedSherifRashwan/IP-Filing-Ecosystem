"""The only module that knows how OpenClaw is invoked.

Two backends hit the same RPC and return the same reply object:

* `gateway call agent --params '<json>'` - params travel in argv, so it is unsafe
  above ~400 KB (macOS ARG_MAX is 1 MiB). Keeps control of the idempotency key.
* `agent --message-file <path>` - documented at 4 MiB, generates its own
  idempotency key. Used automatically for P4-sized prompts.

Ported from `/Users/ahmed/Nucleus/app/providers/openclaw_client.py`: the error
classification, the backoff and the session-key assertion are the same, because
the failure modes are the gateway's, not the application's.

Prompt text is never logged. It is confidential inventor material and it is
already on disk at `runs/<call_id>/prompt.md`.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

from ecosystem.config import Settings
from ecosystem.contracts import extract_json_object

log = logging.getLogger("ipfe")

__all__ = [
    "AgentCliBackend",
    "AutoTransport",
    "CallRequest",
    "GatewayAuthError",
    "GatewayCallBackend",
    "GatewayError",
    "GatewayRateLimited",
    "GatewayReply",
    "GatewayTimeout",
    "GatewayTransientError",
    "PromptTooLarge",
    "Transport",
    "assert_session_key_matches",
    "call_with_backoff",
    "classify_error",
    "session_key",
]


# ---------------------------------------------------------------------------
# Errors - normalised so the runner branches on cause, not on wire detail
# ---------------------------------------------------------------------------


class GatewayError(RuntimeError):
    """Base class for every OpenClaw failure."""


class GatewayTransientError(GatewayError):
    """Retryable: timeout, connection reset, gateway restart, turn still in flight."""


class GatewayTimeout(GatewayTransientError):
    """The CLI did not return within its own timeout plus our grace period."""


class GatewayAuthError(GatewayError):
    """The gateway rejected the token, or upstream provider auth failed."""


class GatewayRateLimited(GatewayError):
    """Subscription or API limit reached. Never silently switch model or provider."""

    def __init__(self, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PromptTooLarge(GatewayError):
    """Bigger than any backend accepts. Fix the input list, do not truncate silently."""


# Substrings that classify a gateway or provider error. Ordered by specificity.
_ERROR_SIGNATURES: Final[tuple[tuple[str, type[GatewayError]], ...]] = (
    ("rate limit", GatewayRateLimited),
    ("session limit", GatewayRateLimited),
    ("hit your", GatewayRateLimited),
    ("rate_limit", GatewayRateLimited),
    ("too many requests", GatewayRateLimited),
    ("429", GatewayRateLimited),
    ("quota", GatewayRateLimited),
    ("credit balance", GatewayRateLimited),
    ("usage limit", GatewayRateLimited),
    ("unauthorized", GatewayAuthError),
    ("forbidden", GatewayAuthError),
    ("invalid token", GatewayAuthError),
    ("auth", GatewayAuthError),
    ("timeout", GatewayTransientError),
    ("was terminated", GatewayTransientError),
    ("produced no output", GatewayTransientError),
    ("timed out", GatewayTransientError),
    ("econnrefused", GatewayTransientError),
    ("connection", GatewayTransientError),
    ("unavailable", GatewayTransientError),
    ("503", GatewayTransientError),
    ("502", GatewayTransientError),
    # The gateway holds one turn per idempotency key. A killed client leaves its
    # turn open and the next call for that key is refused until it settles -
    # transient, not a failure of the request.
    ("in_flight", GatewayTransientError),
    ("already running", GatewayTransientError),
    ("became active in another runner", GatewayTransientError),
    ("another runner", GatewayTransientError),
    ("wait for it to finish", GatewayTransientError),
)

# A held session or an in-flight turn clears in seconds, not minutes.
TRANSIENT_RETRY_ATTEMPTS: Final[int] = 3
TRANSIENT_RETRY_DELAY_SECONDS: Final[int] = 6
# Grace beyond the CLI's own timeout before we kill it.
SUBPROCESS_GRACE_SECONDS: Final[int] = 60
SESSION_KEY_MAX_CHARS: Final[int] = 96


def classify_error(message: str, retryable: bool | None = None) -> GatewayError:
    """Map a gateway error string onto a normalised exception."""
    lowered = (message or "").lower()
    for needle, exc_type in _ERROR_SIGNATURES:
        if needle in lowered:
            return exc_type(message)
    if retryable:
        return GatewayTransientError(message)
    return GatewayError(message)


# ---------------------------------------------------------------------------
# Session keys
# ---------------------------------------------------------------------------

_KEY_CLEAN_RE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9-]+")


def session_key(agent_id: str, suffix: str) -> str:
    """`agent:<id>:<suffix>`, lowercase and `[a-z0-9-]` only.

    Every call gets its own session: cross-call context comes from embedded
    dossier files, never from session history (playbook R1). The gateway also
    refuses a turn on a session another runner holds, so sharing one would make
    an abandoned turn block every later call.
    """
    clean = _KEY_CLEAN_RE.sub("-", suffix.lower()).strip("-")
    prefix = f"agent:{agent_id.lower()}:"
    budget = SESSION_KEY_MAX_CHARS - len(prefix) - 1
    if budget <= 0:
        raise ValueError(f"agent id {agent_id!r} leaves no room for a session suffix")
    return prefix + clean[:budget]


def assert_session_key_matches(agent_id: str, key: str | None) -> None:
    """A session key naming the wrong agent is a coding error, not a runtime one."""
    if not key or not key.startswith("agent:"):
        return
    named = key.split(":", 2)[1]
    if named != agent_id:
        raise ValueError(
            f"session key names agent {named!r} but the turn routes to "
            f"{agent_id!r}; build it with session_key()"
        )


def _retry_key(key: str, attempt: int) -> str:
    """Fresh key per retry. Reusing one would hit the very turn we are retrying past."""
    suffix = f"-r{attempt}"
    if len(key) + len(suffix) > SESSION_KEY_MAX_CHARS:
        key = key[: SESSION_KEY_MAX_CHARS - len(suffix)]
    return key + suffix


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CallRequest:
    """One agent turn. `prompt_path` is written before the call and is the artefact."""

    agent_id: str
    prompt_path: Path
    prompt_text: str
    session_key: str
    idempotency_key: str
    timeout_s: int = 1200
    # Per-call model override (model tiering). Only the `openclaw agent` CLI
    # honours it; the gateway RPC refuses provider/model overrides.
    model: str | None = None

    def with_retry(self, attempt: int) -> CallRequest:
        return CallRequest(
            agent_id=self.agent_id,
            prompt_path=self.prompt_path,
            prompt_text=self.prompt_text,
            session_key=_retry_key(self.session_key, attempt),
            idempotency_key=_retry_key(self.idempotency_key, attempt),
            timeout_s=self.timeout_s,
            model=self.model,
        )


@dataclass
class GatewayReply:
    run_id: str | None
    status: str
    text: str
    provider: str | None = None
    model: str | None = None
    duration_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    transport: str = ""


class Transport(Protocol):
    """What the runner depends on. `tests.fakes.FakeTransport` satisfies it too."""

    name: str

    def call(self, req: CallRequest) -> GatewayReply: ...


# ---------------------------------------------------------------------------
# Reply parsing (identical for both backends)
# ---------------------------------------------------------------------------


def parse_gateway_payload(payload: dict[str, Any], transport: str = "") -> GatewayReply:
    """Turn the RPC reply object into a `GatewayReply`, or raise a classified error."""
    if payload.get("ok") is False:
        error = payload.get("error") or {}
        raise classify_error(
            error.get("message") or "gateway call failed",
            retryable=error.get("retryable"),
        )

    status = str(payload.get("status") or "")
    if status and status != "ok":
        raise classify_error(f"agent turn status={status}: {payload.get('summary') or ''}")

    result = payload.get("result") or {}
    texts = [
        item["text"]
        for item in (result.get("payloads") or [])
        if isinstance(item, dict) and item.get("text")
    ]
    text = "\n".join(texts)
    if not text:
        raise GatewayError("agent turn returned no text payload")

    meta = result.get("meta") or {}
    report = meta.get("systemPromptReport") or {}
    return GatewayReply(
        run_id=payload.get("runId"),
        status=status or "ok",
        text=text,
        provider=report.get("provider"),
        model=report.get("model"),
        duration_ms=int(meta.get("durationMs") or 0),
        raw=payload,
        transport=transport,
    )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class _CliBackend:
    """Shared subprocess handling. Subclasses only build argv."""

    name = "cli"

    def __init__(self, bin_path: str, runner: Any = None) -> None:
        self.bin_path = bin_path
        # Injectable for tests; defaults to the real subprocess transport.
        self._run = runner or subprocess.run

    def build_argv(self, req: CallRequest) -> list[str]:  # pragma: no cover - abstract
        raise NotImplementedError

    def call(self, req: CallRequest) -> GatewayReply:
        assert_session_key_matches(req.agent_id, req.session_key)
        argv = self.build_argv(req)
        started = time.monotonic()
        log.info(
            "call_started agent=%s transport=%s session=%s prompt_chars=%d",
            req.agent_id,
            self.name,
            req.session_key,
            len(req.prompt_text),
        )
        try:
            completed = self._run(
                argv,
                capture_output=True,
                text=True,
                timeout=req.timeout_s + SUBPROCESS_GRACE_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise GatewayTimeout(
                f"{self.name} exceeded {req.timeout_s + SUBPROCESS_GRACE_SECONDS}s "
                f"for agent {req.agent_id}"
            ) from exc
        except FileNotFoundError as exc:
            raise GatewayError(f"openclaw CLI not executable at {self.bin_path}") from exc

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if not stdout:
            raise classify_error(stderr or f"{self.name} returned no output")

        payload = extract_json_object(stdout)
        if payload is None:
            # stderr, not stdout: stdout may contain confidential reply text.
            raise GatewayError(f"{self.name} returned unparseable output ({len(stdout)} chars)")

        reply = parse_gateway_payload(payload, transport=self.name)
        if not reply.duration_ms:
            reply.duration_ms = int((time.monotonic() - started) * 1000)
        if completed.returncode not in (0, None) and reply.status == "ok":
            log.warning("%s exited %s despite status ok", self.name, completed.returncode)
        return reply


class GatewayCallBackend(_CliBackend):
    """`openclaw gateway call agent` - the proven Nucleus transport."""

    name = "gateway-call"

    def params(self, req: CallRequest) -> dict[str, Any]:
        return {
            "agentId": req.agent_id,
            "message": req.prompt_text,
            "idempotencyKey": req.idempotency_key,
            "sessionKey": req.session_key,
            # Same unit as --timeout: the CLI takes milliseconds.
            "timeout": req.timeout_s * 1000,
        }

    def build_argv(self, req: CallRequest) -> list[str]:
        return [
            self.bin_path,
            "gateway",
            "call",
            "agent",
            "--params",
            json.dumps(self.params(req)),
            "--timeout",
            str(req.timeout_s * 1000),
            "--json",
            "--expect-final",
        ]


class AgentCliBackend(_CliBackend):
    """`openclaw agent --message-file` - for prompts too large for argv."""

    name = "agent-cli"

    def build_argv(self, req: CallRequest) -> list[str]:
        return [
            self.bin_path,
            "agent",
            "--agent",
            req.agent_id,
            "--session-key",
            req.session_key,
            "--message-file",
            str(req.prompt_path),
            "--json",
            "--timeout",
            str(req.timeout_s),
        ] + (["--model", req.model] if req.model else [])

    def call(self, req: CallRequest) -> GatewayReply:
        # The file is the message here; a missing one would send an empty turn.
        if not req.prompt_path.is_file():
            raise GatewayError(f"prompt file does not exist: {req.prompt_path}")
        return super().call(req)


# ---------------------------------------------------------------------------
# Auto selection + backoff
# ---------------------------------------------------------------------------


class AutoTransport:
    """Picks a backend by serialized size, then retries transient failures."""

    name = "auto"

    def __init__(
        self,
        settings: Settings,
        force: str | None = None,
        runner: Any = None,
        bin_path: str | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.settings = settings
        self.force = force if force in {"gateway-call", "agent-cli"} else None
        resolved = bin_path or settings.resolve_openclaw_bin()
        self.gateway = GatewayCallBackend(resolved, runner=runner)
        self.agent_cli = AgentCliBackend(resolved, runner=runner)
        self._sleep = sleep

    def params_bytes(self, req: CallRequest) -> int:
        """Exact argv cost of the gateway backend - the thing ARG_MAX limits."""
        return len(json.dumps(self.gateway.params(req)).encode("utf-8"))

    def select(self, req: CallRequest) -> _CliBackend:
        size = self.params_bytes(req)
        if req.model:
            # The gateway RPC answers "provider/model overrides are not
            # authorized for this caller"; the CLI path is the one that works.
            if size > self.settings.agent_cli_max_bytes:
                raise PromptTooLarge(
                    f"prompt for {req.agent_id} serialises to {size} bytes, over the "
                    f"{self.settings.agent_cli_max_bytes} byte agent-cli limit"
                )
            return self.agent_cli
        if size > self.settings.agent_cli_max_bytes:
            raise PromptTooLarge(
                f"prompt for {req.agent_id} serialises to {size} bytes, over the "
                f"{self.settings.agent_cli_max_bytes} byte agent-cli limit; "
                "reduce the input list"
            )
        if self.force == "gateway-call":
            if size > self.settings.gateway_call_max_bytes:
                raise PromptTooLarge(
                    f"--transport gateway-call requested but params are {size} bytes, "
                    f"over the {self.settings.gateway_call_max_bytes} byte argv limit"
                )
            return self.gateway
        if self.force == "agent-cli":
            return self.agent_cli
        return self.gateway if size <= self.settings.gateway_call_max_bytes else self.agent_cli

    def call(self, req: CallRequest) -> GatewayReply:
        backend = self.select(req)
        return call_with_backoff(backend, req, sleep=self._sleep)


def call_with_backoff(
    backend: Transport,
    req: CallRequest,
    attempts: int = TRANSIENT_RETRY_ATTEMPTS,
    base_delay: float = TRANSIENT_RETRY_DELAY_SECONDS,
    sleep: Any = time.sleep,
) -> GatewayReply:
    """One turn, retried while the gateway is momentarily unable to take it.

    A session another runner still holds, or a turn left in flight by a client
    that died, clears on its own. Failing the run over it discards work that was
    only ever waiting. Each retry gets fresh session and idempotency keys so it
    is a new turn, not a collision with the one being retried.
    """
    delay = base_delay
    for attempt in range(attempts):
        try:
            return backend.call(req if attempt == 0 else req.with_retry(attempt))
        except GatewayTransientError as exc:
            if attempt == attempts - 1:
                raise
            log.warning(
                "gateway_transient_retry agent=%s attempt=%d delay=%.0fs error=%s",
                req.agent_id,
                attempt + 1,
                delay,
                str(exc)[:160],
            )
            sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")
