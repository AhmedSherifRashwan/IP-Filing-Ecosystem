"""Gates: pending files, side effects, idempotency, and who may decide."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecosystem.config import Settings
from ecosystem.dossier import Dossier, split_frontmatter
from ecosystem.gates import GateError, GateRefused, Gates, gate_stem, options_for
from ecosystem.log import EventLog
from ecosystem.state import MatterState, new_state
from tests.conftest import write_version
from tests.replay_builder import TERMINOLOGY_TABLE

P1_04 = f"""## Deliverable

### Terminology Table

{TERMINOLOGY_TABLE}

## Rationale

Ranges were kept broad.

## Assumptions

- A1: none.

## Open Questions

- Q1: none.
"""

P3_BODY = """## Deliverable

### Design-Around Risk Register

The competitor replaces the seat ring 30 with a stack of washers.

## Rationale

Not shown to the reviewer.

## Assumptions

- A1: none.

## Open Questions

- Q1: none.
"""


@pytest.fixture
def state(dossier: Dossier) -> MatterState:
    return new_state(dossier.slug, "Demo matter", ["US"])


@pytest.fixture
def gates(dossier: Dossier, state: MatterState, settings: Settings) -> Gates:
    return Gates(dossier, state, settings, EventLog(dossier.log_path, dossier.slug))


def _p1(dossier: Dossier, state: MatterState) -> None:
    v = write_version(dossier, "P1", {"04": P1_04})
    state.phase("P1").current_version = v
    state.phase("P1").status = "complete"


def _p3(dossier: Dossier, state: MatterState) -> int:
    v = write_version(dossier, "P3", {"11": P3_BODY, "12": P3_BODY})
    state.phase("P3").current_version = v
    state.phase("P3").status = "complete"
    return v


# ---------------------------------------------------------------------------
# request
# ---------------------------------------------------------------------------


def test_request_writes_the_pending_file_and_blocks_the_run(
    dossier: Dossier, state: MatterState, gates: Gates
) -> None:
    path = gates.request("1", {"summary": "confirm the disclosure"}, ["P2", "P3"])

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["gate"] == "1"
    assert record["options"] == ["accept", "reject"]
    assert record["blocks"] == ["P2", "P3"]
    assert record["payload"]["summary"] == "confirm the disclosure"
    assert state.status == "awaiting_gate"
    assert state.gate("1").status == "pending"


def test_cap_gates_block_rather_than_await(state: MatterState, gates: Gates) -> None:
    gates.request("loopback-cap-novelty", {"category": "novelty"}, ["packaging"])
    assert state.status == "blocked"
    assert options_for("loopback-cap-novelty") == [
        "accept-residual",
        "manual-fix",
        "narrow",
        "abort",
    ]


def test_a_failing_brief_never_blocks_the_gate(
    dossier: Dossier, state: MatterState, settings: Settings
) -> None:
    def explode(gate: str, record: dict) -> str:
        raise RuntimeError("gateway is down")

    gates = Gates(
        dossier, state, settings, EventLog(dossier.log_path), brief_writer=explode
    )
    gates.request("1", {"files": ["01-disclosure/v1/01.md"]}, ["P2"])

    brief = (dossier.briefs_dir / "gate-1.md").read_text(encoding="utf-8")
    assert "01-disclosure/v1/01.md" in brief
    assert "accept" in brief
    assert state.gate("1").status == "pending"


def test_brief_from_the_orchestrator_is_written_verbatim(
    dossier: Dossier, state: MatterState, settings: Settings
) -> None:
    gates = Gates(
        dossier,
        state,
        settings,
        EventLog(dossier.log_path),
        brief_writer=lambda gate, record: f"# Brief for gate {gate}\n\nAccept it.",
    )
    gates.request("2", {}, [])
    assert "Accept it." in (dossier.briefs_dir / "gate-2.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# decide - validation
# ---------------------------------------------------------------------------


def test_deciding_a_gate_that_is_not_pending_is_refused(gates: Gates) -> None:
    with pytest.raises(GateError):
        gates.decide("1", "accept", "ahmed")


def test_unknown_decision_is_refused(gates: Gates) -> None:
    gates.request("1", {}, [])
    with pytest.raises(GateRefused):
        gates.decide("1", "maybe", "ahmed")


def test_agent_ids_may_not_decide_a_gate(
    dossier: Dossier, state: MatterState, gates: Gates
) -> None:
    _p1(dossier, state)
    gates.request("3", {}, ["filing"])
    with pytest.raises(GateRefused, match="names an agent"):
        gates.decide("3", "accept", "ip-orchestrator")
    with pytest.raises(GateRefused):
        gates.decide("3", "accept", "   ")


def test_repeating_the_same_decision_is_idempotent(
    dossier: Dossier, state: MatterState, gates: Gates
) -> None:
    _p1(dossier, state)
    gates.request("1", {}, [])
    first = gates.decide("1", "accept", "ahmed")
    second = gates.decide("1", "accept", "ahmed")

    assert first.status == "decided"
    assert second.status == "already_decided"
    assert second.effects == []


def test_contradicting_a_decision_is_refused(
    dossier: Dossier, state: MatterState, gates: Gates
) -> None:
    _p1(dossier, state)
    gates.request("1", {}, [])
    gates.decide("1", "accept", "ahmed")
    with pytest.raises(GateRefused, match="already decided"):
        gates.decide("1", "reject", "ahmed")


# ---------------------------------------------------------------------------
# Gate 1 - terminology lock
# ---------------------------------------------------------------------------


def test_gate1_accept_locks_the_terminology(
    dossier: Dossier, state: MatterState, gates: Gates
) -> None:
    _p1(dossier, state)
    gates.request("1", {}, ["P2"])
    result = gates.decide("1", "accept", "ahmed")

    locked = dossier.locked_terminology_path.read_text(encoding="utf-8")
    assert "seat ring" in locked
    parsed = json.loads((dossier.terminology_dir / "LOCKED.json").read_text(encoding="utf-8"))
    assert [row["preferred"] for row in parsed["rows"]][0] == "seat ring"
    assert state.terminology.terms == 4
    assert state.terminology.sha256
    assert any("locked" in effect for effect in result.effects)
    assert not (dossier.gates_dir / "gate-1.pending.json").exists()
    assert (dossier.gates_dir / "gate-1.decided.json").is_file()


def test_gate1_keeps_a_human_edited_lock(
    dossier: Dossier, state: MatterState, gates: Gates
) -> None:
    _p1(dossier, state)
    edited = "| Preferred term | Terms to avoid |\n| --- | --- |\n| purge gap | escape hole |"
    dossier.write_file(dossier.locked_terminology_path, edited)
    gates.request("1", {}, [])
    result = gates.decide("1", "accept", "ahmed")

    assert dossier.locked_terminology_path.read_text(encoding="utf-8") == edited
    assert any("human-edited" in effect for effect in result.effects)
    assert state.terminology.terms == 1


def test_gate1_copies_the_answers_file(
    dossier: Dossier, state: MatterState, gates: Gates, tmp_path: Path
) -> None:
    _p1(dossier, state)
    answers = tmp_path / "answers.md"
    answers.write_text("A1: yes, the alloy is fixed.", encoding="utf-8")
    gates.request("1", {}, [])
    gates.decide("1", "accept", "ahmed", answers_file=answers)

    copied = dossier.intake_dir / "gate-1-answers.md"
    assert copied.read_text(encoding="utf-8").startswith("A1: yes")


def test_gate1_without_prompt_04_cannot_lock(
    dossier: Dossier, state: MatterState, gates: Gates
) -> None:
    gates.request("1", {}, [])
    with pytest.raises(GateError, match="no Prompt 04 output"):
        gates.decide("1", "accept", "ahmed")


def test_gate1_reject_fails_the_matter(
    dossier: Dossier, state: MatterState, gates: Gates
) -> None:
    _p1(dossier, state)
    gates.request("1", {}, [])
    gates.decide("1", "reject", "ahmed", note="the disclosure is wrong")

    assert state.status == "failed"
    record = json.loads((dossier.gates_dir / "gate-1.decided.json").read_text(encoding="utf-8"))
    assert record["note"] == "the disclosure is wrong"


# ---------------------------------------------------------------------------
# Gate 2 - confirmed embodiments
# ---------------------------------------------------------------------------


def test_gate2_accept_writes_confirmed_and_flips_status(
    dossier: Dossier, state: MatterState, gates: Gates
) -> None:
    v = _p3(dossier, state)
    gates.request("2", {}, ["P4"])
    gates.decide("2", "accept", "ahmed")

    confirmed = dossier.version("P3", v).path / "CONFIRMED.md"
    text = confirmed.read_text(encoding="utf-8")
    assert "stack of washers" in text
    assert text.count("stack of washers") == 2  # one per confirmed output
    for path in dossier.version("P3", v).path.glob("1*.md"):
        frontmatter, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        assert frontmatter["status"] == "confirmed"


def test_gate2_confirmed_file_wins_verbatim(
    dossier: Dossier, state: MatterState, gates: Gates, tmp_path: Path
) -> None:
    v = _p3(dossier, state)
    override = tmp_path / "confirmed.md"
    override.write_text("Only embodiment 3 is real.", encoding="utf-8")
    gates.request("2", {}, [])
    gates.decide("2", "accept", "ahmed", confirmed_file=override)

    confirmed = dossier.version("P3", v).path / "CONFIRMED.md"
    assert confirmed.read_text(encoding="utf-8").strip() == "Only embodiment 3 is real."


def test_gate2_delta_carries_the_previous_confirmed_material(
    dossier: Dossier, state: MatterState, gates: Gates
) -> None:
    _p3(dossier, state)
    gates.request("2", {}, [])
    gates.decide("2", "accept", "ahmed")

    v2 = write_version(dossier, "P3", {"x-loopback-p3": P3_BODY}, trigger="loopback:novelty:1")
    state.phase("P3").current_version = v2
    gates.request("2", {"delta": True}, [])
    gates.decide("2", "accept", "ahmed")

    text = (dossier.version("P3", v2).path / "CONFIRMED.md").read_text(encoding="utf-8")
    assert "Previously confirmed" in text
    # The earlier decision is archived, not overwritten.
    assert (dossier.gates_dir / "gate-2.decided.r1.json").is_file()
    assert (dossier.gates_dir / "gate-2.decided.json").is_file()


# ---------------------------------------------------------------------------
# Gate 3 and cap gates
# ---------------------------------------------------------------------------


def test_gate3_accept_completes_the_matter(state: MatterState, gates: Gates) -> None:
    gates.request("3", {}, ["filing"])
    gates.decide("3", "accept", "counsel")
    assert state.status == "complete"


def test_cap_gate_abort_fails_the_matter(state: MatterState, gates: Gates) -> None:
    gates.request("loopback-cap-novelty", {}, [])
    gates.decide("loopback-cap-novelty", "abort", "ahmed", note="not worth filing")

    assert state.status == "failed"
    assert state.loopback.forced_decisions[-1].decision == "abort"


def test_cap_gate_accept_residual_lets_the_run_continue(
    state: MatterState, gates: Gates
) -> None:
    gates.request("loopback-cap-novelty", {}, [])
    result = gates.decide("loopback-cap-novelty", "accept-residual", "ahmed")

    assert state.status == "idle"
    assert any("residual" in effect for effect in result.effects)
    assert state.loopback.forced_decisions[-1].applied is False


def test_cap_gate_manual_fix_carries_edits_into_a_new_version(
    dossier: Dossier, state: MatterState, gates: Gates
) -> None:
    v = write_version(dossier, "P5", {"25": P3_BODY}, extra_files={"claims.md": "1. A thing."})
    state.phase("P5").current_version = v
    gates.request("loopback-cap-support", {}, [])
    gates.decide("loopback-cap-support", "manual-fix", "ahmed")

    latest = dossier.versions("P5")[-1]
    assert latest.v == v + 1
    assert latest.read_version_json()["trigger"] == "manual-fix"
    assert (latest.path / "claims.md").read_text(encoding="utf-8") == "1. A thing."


def test_gate_stem_naming() -> None:
    assert gate_stem("1") == "gate-1"
    assert gate_stem("loopback-cap-novelty") == "loopback-cap-novelty"
