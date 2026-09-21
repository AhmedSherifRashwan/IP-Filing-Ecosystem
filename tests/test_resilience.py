"""One bad reply must never end an unattended run: retry, then fall back."""

from __future__ import annotations

from pathlib import Path

import pytest

from ecosystem.calls import Caller, ContractFailed
from ecosystem.config import Settings
from ecosystem.contracts import reply_looks_defective
from ecosystem.dossier import Dossier
from ecosystem.log import EventLog
from ecosystem.playbook import Playbook, load_playbook
from ecosystem.state import MatterState, new_state
from tests.fakes import FakeTransport
from tests.replay_builder import drafting_reply
from tests.test_calls import _fixture, _lock, _spec, _version


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


def test_defect_detection() -> None:
    assert reply_looks_defective("", "drafting") == "empty"
    assert reply_looks_defective("ok", "drafting") == "empty"
    tail = "as a value reservation 1316 against the remaining " * 80
    assert reply_looks_defective(tail, "drafting") == "head-truncated"
    assert reply_looks_defective("## Deliverable\n\n" + tail, "drafting") is None
    assert reply_looks_defective("## Findings\n\n" + tail, "review") is None
    assert reply_looks_defective(tail, "prose") is None


def test_truncated_reply_is_retried_in_a_fresh_session(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    _lock(dossier)
    spec = _spec(phase="P3", agent="ip-embodiment")
    _fixture(replays, "demo-p3-t1-v1", "tail of a long answer " * 200)
    _fixture(replays, "demo-p3-t1-v1-fresh1", drafting_reply("t1", ["Section"]))
    transport = FakeTransport(fixtures_dir=replays)
    result = Caller(
        dossier, state, playbook, transport, settings, EventLog(dossier.log_path, "demo")
    ).execute(spec, _version(dossier))
    assert result.parsed is not None and not result.skipped
    assert len(transport.calls) == 2
    assert transport.calls[-1].prefer_gateway is True
    assert transport.calls[-1].session_key.endswith("-fresh1")


def _always_bad(replays: Path, key: str) -> None:
    # Short and wrongly structured: fails the contract without looking truncated.
    for suffix in ("", ".turn2", "-rerun1", "-rerun1.turn2"):
        _fixture(replays, key + suffix, "## Summary\n\nNot the contract.\n")


def test_failed_revision_is_skipped_not_fatal(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    _lock(dossier)
    spec = _spec(id="x-p4-revise", phase="P4", agent="ip-spec", kind="revise")
    _always_bad(replays, "demo-p4-x-p4-revise-v1")
    transport = FakeTransport(fixtures_dir=replays)
    result = Caller(
        dossier, state, playbook, transport, settings, EventLog(dossier.log_path, "demo")
    ).execute(spec, _version(dossier, "P4"))
    assert result.skipped is True
    assert result.output_path is None
    assert state.agent_runs[-1].status == "skipped"


def test_failed_noncritical_prompt_keeps_a_partial_output(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    _lock(dossier)
    spec = _spec(id="13", phase="P3", agent="ip-embodiment")
    _always_bad(replays, "demo-p3-13-v1")
    transport = FakeTransport(fixtures_dir=replays)
    result = Caller(
        dossier, state, playbook, transport, settings, EventLog(dossier.log_path, "demo")
    ).execute(spec, _version(dossier))
    assert result.partial is True and result.output_path is not None
    assert "status: partial" in result.output_path.read_text()


def test_failed_critical_prompt_still_stops(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    _lock(dossier)
    spec = _spec(id="16", phase="P4", agent="ip-spec")
    _always_bad(replays, "demo-p4-16-v1")
    transport = FakeTransport(fixtures_dir=replays)
    with pytest.raises(ContractFailed):
        Caller(
            dossier, state, playbook, transport, settings, EventLog(dossier.log_path, "demo")
        ).execute(spec, _version(dossier, "P4"))
