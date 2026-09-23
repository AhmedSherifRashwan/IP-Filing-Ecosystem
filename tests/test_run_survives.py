"""One blip must never end a run that could have continued."""

from __future__ import annotations

from pathlib import Path

import pytest

from ecosystem.calls import Caller
from ecosystem.config import Settings
from ecosystem.dossier import Dossier
from ecosystem.log import EventLog
from ecosystem.playbook import Playbook, load_playbook
from ecosystem.prompt_builder import assemble, lost_steps
from ecosystem.state import AgentRun, MatterState, new_state
from ecosystem.transport import GatewayAuthError, GatewayTransientError, classify_error
from tests.replay_builder import drafting_reply
from tests.test_calls import _spec, _version


@pytest.fixture
def playbook(settings: Settings) -> Playbook:
    return load_playbook(settings.prompts_path)


@pytest.fixture
def state(dossier: Dossier) -> MatterState:
    return new_state(dossier.slug, "Demo", ["US"])


@pytest.fixture
def replays(tmp_path: Path) -> Path:
    directory = tmp_path / "replays"
    directory.mkdir()
    return directory


def _caller(dossier, state, playbook, settings, transport) -> Caller:
    return Caller(dossier, state, playbook, transport, settings, EventLog(dossier.log_path, "demo"))


class _Flaky:
    """Fails the first n calls, then answers."""

    def __init__(self, error: Exception, fails: int, reply: str) -> None:
        self.error = error
        self.fails = fails
        self.reply = reply
        self.calls: list[object] = []

    def call(self, req: object) -> object:
        self.calls.append(req)
        if len(self.calls) <= self.fails:
            raise self.error
        from ecosystem.transport import GatewayReply

        return GatewayReply(
            run_id="r", status="ok", text=self.reply, provider="p", model="m", duration_ms=1
        )


def test_a_failed_token_refresh_is_transient_not_a_verdict() -> None:
    assert isinstance(
        classify_error("auth refresh request failed: code=-32603"), GatewayTransientError
    )
    assert isinstance(classify_error("unauthorized: bad token"), GatewayAuthError)


def test_the_tier_model_falling_over_does_not_lose_the_step(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings
) -> None:
    """Tiering is an economy: if the second provider will not answer, use the first."""
    flaky = _Flaky(GatewayAuthError("auth refused"), fails=1, reply=drafting_reply("02", ["S"]))
    caller = _caller(dossier, state, playbook, settings, flaky)
    spec = _spec(id="02", phase="P1", agent="ip-disclosure", deliverable_sections=["S"])

    result = caller.execute(spec, _version(dossier, "P1"))

    assert result.parsed is not None and not result.skipped
    assert len(flaky.calls) == 2
    assert flaky.calls[0].model == "openai/gpt-5.6-sol"  # type: ignore[attr-defined]
    assert flaky.calls[1].model is None  # the agent's own model
    assert state.agent_runs[-1].status == "ok"


def test_a_provider_that_never_answers_costs_the_step_not_the_run(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings
) -> None:
    dead = _Flaky(GatewayAuthError("unauthorized"), fails=99, reply="")
    caller = _caller(dossier, state, playbook, settings, dead)
    spec = _spec(id="02", phase="P1", agent="ip-disclosure")

    result = caller.execute(spec, _version(dossier, "P1"))

    assert result.skipped is True  # recorded, not raised
    assert state.agent_runs[-1].status == "failed"
    assert state.counters.get("steps_lost") == 1


def test_a_step_that_cannot_be_replaced_still_stops_the_run(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings
) -> None:
    dead = _Flaky(GatewayAuthError("unauthorized"), fails=99, reply="")
    caller = _caller(dossier, state, playbook, settings, dead)
    spec = _spec(id="16", phase="P4", agent="ip-spec")  # the specification blueprint

    with pytest.raises(GatewayAuthError):
        caller.execute(spec, _version(dossier, "P4"))


def test_a_later_step_is_told_its_input_is_absent_rather_than_stopped(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings
) -> None:
    state.agent_runs.append(
        AgentRun(
            call_id="demo-p1-02-v1-a0",
            phase="P1",
            prompt="02",
            agent_id="ip-disclosure",
            session_key="agent:ip-disclosure:demo",
            idempotency_key="k",
            status="failed",
        )
    )
    assert "out:P1/02" in lost_steps(state)

    (dossier.intake_dir / "disclosure.md").write_text("An invention.", encoding="utf-8")
    from ecosystem.dossier import InputContext

    spec = _spec(
        id="03", phase="P1", agent="ip-disclosure", inputs=["intake:disclosure", "out:P1/02"]
    )
    assembled = assemble(dossier, state, spec, InputContext(phase="P1", prompt_id="03"), settings)
    assert "This input is missing" in assembled.text
    assert "say in your Open Questions that this input was absent" in assembled.text
