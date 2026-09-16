"""Record/replay transport, so the whole pipeline can be tested without OpenClaw.

Replay is keyed by call id (the session-key suffix), because that is the only
identifier that is stable across runs of the same matter. The prompt hash is the
fallback for calls whose session key was not built by the runner.

A missing fixture is an `AssertionError` naming the key and listing what is
available: a replay test that silently invents a reply is worse than no test.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ecosystem.transport import CallRequest, GatewayReply, Transport, parse_gateway_payload

__all__ = ["FakeTransport", "call_id_for", "make_payload"]


def call_id_for(req: CallRequest) -> str:
    """`agent:<id>:<suffix>` -> `<suffix>`; anything else -> sha256 of the prompt."""
    key = req.session_key or ""
    if key.startswith("agent:") and key.count(":") >= 2:
        suffix = key.split(":", 2)[2]
        if suffix:
            return suffix
    return hashlib.sha256(req.prompt_text.encode("utf-8")).hexdigest()


def make_payload(
    text: str,
    run_id: str = "run-fake",
    provider: str = "fake",
    model: str = "fake/model",
    duration_ms: int = 1234,
) -> dict[str, Any]:
    """The reply object both real backends return - used to author fixtures."""
    return {
        "runId": run_id,
        "status": "ok",
        "summary": "",
        "result": {
            "payloads": [{"text": text}],
            "meta": {
                "durationMs": duration_ms,
                "systemPromptReport": {"provider": provider, "model": model},
            },
        },
    }


class FakeTransport:
    """Satisfies `ecosystem.transport.Transport`.

    `replay` reads `<fixtures_dir>/<call_id>.json`; `record` wraps a real
    transport and writes the same files, so a live smoke run can be frozen into
    a regression suite.
    """

    name = "fake"

    def __init__(
        self,
        mode: str = "replay",
        fixtures_dir: Path | str = Path("tests/fixtures/replays"),
        inner: Transport | None = None,
    ) -> None:
        if mode not in {"replay", "record"}:
            raise ValueError(f"mode must be 'replay' or 'record', not {mode!r}")
        if mode == "record" and inner is None:
            raise ValueError("record mode needs a real transport to wrap")
        self.mode = mode
        self.fixtures_dir = Path(fixtures_dir)
        self.inner = inner
        self.calls: list[CallRequest] = []
        # How many turns each session has already taken. A correction turn
        # reuses the session key by design, so the only way to replay "bad
        # reply, then corrected reply" is to count turns per key.
        self.turns: dict[str, int] = {}

    # -- authoring helpers --------------------------------------------------

    def write_fixture(self, call_id: str, text: str, **payload_kwargs: Any) -> Path:
        """Create a replay fixture from reply text. Used by tests to set a scenario."""
        self.fixtures_dir.mkdir(parents=True, exist_ok=True)
        path = self.fixtures_dir / f"{call_id}.json"
        path.write_text(
            json.dumps(
                {
                    "call_id": call_id,
                    "agent_id": payload_kwargs.pop("agent_id", "fake-agent"),
                    "session_key": payload_kwargs.pop("session_key", f"agent:fake-agent:{call_id}"),
                    "prompt_sha256": payload_kwargs.pop("prompt_sha256", ""),
                    "response": make_payload(text, **payload_kwargs),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def available(self) -> list[str]:
        if not self.fixtures_dir.is_dir():
            return []
        return sorted(p.stem for p in self.fixtures_dir.glob("*.json"))

    # -- transport ----------------------------------------------------------

    def call(self, req: CallRequest) -> GatewayReply:
        self.calls.append(req)
        return self._record(req) if self.mode == "record" else self._replay(req)

    def _replay(self, req: CallRequest) -> GatewayReply:
        call_id = call_id_for(req)
        turn = self.turns.get(call_id, 0) + 1
        self.turns[call_id] = turn
        candidates = [
            # `<call_id>.turn<N>.json` wins when present: that is how a test
            # scripts a contract failure followed by a good correction.
            f"{call_id}.turn{turn}",
            call_id,
            hashlib.sha256(req.prompt_text.encode()).hexdigest(),
        ]
        for key in candidates:
            path = self.fixtures_dir / f"{key}.json"
            if path.is_file():
                fixture = json.loads(path.read_text(encoding="utf-8"))
                reply = parse_gateway_payload(fixture["response"], transport="fake")
                return reply
        raise AssertionError(
            f"no replay fixture for call_id {call_id_for(req)!r} "
            f"(agent {req.agent_id}) in {self.fixtures_dir}; "
            f"available: {', '.join(self.available()) or '<none>'}"
        )

    def _record(self, req: CallRequest) -> GatewayReply:
        assert self.inner is not None
        reply = self.inner.call(req)
        self.fixtures_dir.mkdir(parents=True, exist_ok=True)
        call_id = call_id_for(req)
        (self.fixtures_dir / f"{call_id}.json").write_text(
            json.dumps(
                {
                    "call_id": call_id,
                    "agent_id": req.agent_id,
                    "session_key": req.session_key,
                    "prompt_sha256": hashlib.sha256(req.prompt_text.encode()).hexdigest(),
                    "response": reply.raw,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return reply
