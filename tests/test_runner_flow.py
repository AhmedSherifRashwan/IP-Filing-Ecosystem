"""The whole pipeline, replayed: new matter -> Gate 3, through `FakeTransport`.

This is the test that would have caught every sequencing bug the plan worries
about. It replays one complete matter and asserts on the things a human depends
on: that the run stops at each gate rather than deciding for itself, that the
inner loop converges and is capped, that a critical finding really does go back
through Phase 3 with a fresh Gate 2, that a formal correction never costs a
loop-back cycle, and that the reviewer never sees drafting rationale.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecosystem.config import Settings
from ecosystem.dossier import Dossier, split_frontmatter
from ecosystem.gates import Gates
from ecosystem.log import EventLog
from ecosystem.playbook import load_playbook
from ecosystem.runner import Runner, StopReason
from ecosystem.state import MatterState, new_state
from ecosystem.state import load as load_state
from ecosystem.state import save as save_state
from tests.conftest import REPO_ROOT
from tests.fakes import FakeTransport
from tests.replay_builder import (
    Scenario,
    claims_correction,
    fixture_key,
    gate14_answers,
    materialise,
    novelty_finding,
)


@pytest.fixture
def playbook(settings: Settings):
    return load_playbook(settings.prompts_path)


@pytest.fixture
def scenario() -> Scenario:
    """The replay: one unmet disclosure request, one critical finding, one correction."""
    return (
        Scenario(slug="demo")
        # Round 1 does not converge: the claims drafter still needs disclosure.
        .at(
            fixture_key("demo", "P5", "27", 1),
            envelope_extra={
                "disclosure_requests": [
                    {
                        "limitation": "purge gap opens above a threshold differential",
                        "needed_disclosure": "an explicit statement of the threshold range",
                    }
                ]
            },
        )
        # Round 2: the claims drafter flags functional language, so 26 must run.
        .at(
            fixture_key("demo", "P5", "x-p5-revise", 2),
            envelope_extra={
                "support_refs": [
                    {"item": "means for purging", "support": "functional language in claim 5"}
                ]
            },
        )
        # Review round 1: one critical novelty finding and one formal correction.
        .at(fixture_key("demo", "REVIEW", "32", 1), findings=[novelty_finding()])
        .at(fixture_key("demo", "REVIEW", "37", 1), corrections=[claims_correction()])
        .at(fixture_key("demo", "REVIEW", "gate14", 2), gate14=gate14_answers())
    )


@pytest.fixture
def replays(tmp_path: Path, playbook, scenario: Scenario) -> Path:
    directory = tmp_path / "replays"
    materialise(playbook, directory, scenario)
    return directory


@pytest.fixture
def matter(settings: Settings) -> Dossier:
    root = settings.dossier_root("demo")
    return Dossier(root, settings).create(
        "demo",
        "Self-purging bicycle tube valve",
        ["US", "EP"],
        from_example=REPO_ROOT / "tests" / "fixtures" / "example-matter",
    )


class Harness:
    """One matter, one replay directory, and a fresh Runner per `run()`.

    A fresh Runner each time is the point: the real CLI builds one per
    invocation, so anything the pipeline remembers has to be in `state.json`.
    """

    def __init__(self, dossier: Dossier, playbook, settings: Settings, replays: Path) -> None:
        self.dossier = dossier
        self.playbook = playbook
        self.settings = settings
        self.replays = replays
        self.transport = FakeTransport(mode="replay", fixtures_dir=replays)

    @property
    def state(self) -> MatterState:
        return load_state(self.dossier.state_path)

    def run(self, **kwargs) -> StopReason:
        state = self.state
        runner = Runner(
            self.dossier,
            state,
            self.playbook,
            self.transport,
            self.settings,
            EventLog(self.dossier.log_path, self.dossier.slug),
        )
        reason = runner.run(**kwargs)
        save_state(state, self.dossier.state_path)
        return reason

    def decide(self, gate: str, decision: str, by: str = "ahmed", **kwargs) -> None:
        state = self.state
        gates = Gates(
            self.dossier, state, self.settings, EventLog(self.dossier.log_path, self.dossier.slug)
        )
        gates.decide(gate, decision, by, **kwargs)
        save_state(state, self.dossier.state_path)

    def sessions(self) -> list[str]:
        return [req.session_key for req in self.transport.calls]

    def prompts_called(self) -> list[str]:
        return [key.split(":", 2)[2] for key in self.sessions()]


@pytest.fixture
def harness(matter: Dossier, playbook, settings: Settings, replays: Path) -> Harness:
    save_state(
        new_state("demo", "Self-purging bicycle tube valve", ["US", "EP"]),
        matter.state_path,
    )
    return Harness(matter, playbook, settings, replays)


# ---------------------------------------------------------------------------
# Phase 1 and Gate 1
# ---------------------------------------------------------------------------


def test_run_stops_at_gate_1(harness: Harness) -> None:
    reason = harness.run()

    assert reason is StopReason.GATE
    state = harness.state
    assert state.status == "awaiting_gate"
    assert state.gate("1").status == "pending"
    assert state.phase("P1").status == "complete"
    assert harness.prompts_called()[:5] == [
        "demo-p1-01-v1",
        "demo-p1-02-v1",
        "demo-p1-03-v1",
        "demo-p1-04-v1",
        "demo-p1-05-v1",
    ]
    # The gate brief is the sixth call, and it goes to the orchestrator.
    assert harness.transport.calls[5].agent_id == "ip-orchestrator"
    assert (harness.dossier.briefs_dir / "gate-1.md").is_file()
    assert (harness.dossier.gates_dir / "gate-1.pending.json").is_file()


def test_phase_1_writes_versioned_outputs_with_frontmatter(harness: Harness) -> None:
    harness.run()
    version = harness.dossier.latest("P1")
    assert version is not None

    output = version.path / "01-invention-extraction.md"
    frontmatter, body = split_frontmatter(output.read_text(encoding="utf-8"))
    assert frontmatter["agent"] == "ip-disclosure"
    assert frontmatter["status"] == "candidate"
    assert frontmatter["version"] == version.v
    assert frontmatter["call_id"] == "demo-p1-01-v1-a0"
    assert [ref["path"] for ref in frontmatter["inputs"]]
    assert body.startswith("## Deliverable")
    assert (version.path / "01.envelope.json").is_file()


def test_every_call_leaves_its_four_run_files(harness: Harness) -> None:
    harness.run()
    run_dir = harness.dossier.runs_dir / "demo-p1-01-v1-a0"
    assert {p.name for p in run_dir.iterdir()} == {
        "prompt.md",
        "reply.md",
        "reply.raw.json",
        "meta.json",
    }
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["agent_id"] == "ip-disclosure"
    assert meta["prompt_chars"] > 0


def test_resuming_before_the_gate_is_decided_does_not_recall_phase_1(
    harness: Harness,
) -> None:
    harness.run()
    before = len(harness.transport.calls)
    assert harness.run() is StopReason.GATE
    assert len(harness.transport.calls) == before


# ---------------------------------------------------------------------------
# through Gate 2
# ---------------------------------------------------------------------------


def _to_gate2(harness: Harness) -> StopReason:
    harness.run()
    harness.decide("1", "accept")
    return harness.run()


def test_gate_1_locks_the_terminology_and_the_run_continues(harness: Harness) -> None:
    reason = _to_gate2(harness)

    assert reason is StopReason.GATE
    state = harness.state
    assert state.gate("2").status == "pending"
    assert state.phase("P2").status == "complete"
    assert state.phase("P3").status == "complete"
    assert state.terminology.terms == 4
    assert harness.dossier.locked_terminology_path.is_file()


def test_locked_terminology_is_embedded_in_every_later_prompt(harness: Harness) -> None:
    _to_gate2(harness)
    prompt = (harness.dossier.runs_dir / "demo-p3-11-v1-a0" / "prompt.md").read_text(
        encoding="utf-8"
    )
    assert "## Locked terminology" in prompt
    assert "seat ring" in prompt


def test_gate_2_confirms_the_embodiments(harness: Harness) -> None:
    _to_gate2(harness)
    harness.decide("2", "accept")

    version = harness.dossier.latest("P3")
    confirmed = version.path / "CONFIRMED.md"
    assert confirmed.is_file()
    for path in version.path.glob("1*.md"):
        frontmatter, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        assert frontmatter["status"] == "confirmed"


# ---------------------------------------------------------------------------
# inner loop
# ---------------------------------------------------------------------------


def _to_review(harness: Harness) -> StopReason:
    _to_gate2(harness)
    harness.decide("2", "accept")
    return harness.run(until="review")


def test_inner_loop_converges_in_two_rounds(harness: Harness) -> None:
    _to_review(harness)
    loop = harness.state.inner_loop

    assert loop.round == 2
    assert loop.converged is True
    assert loop.rounds[0].disclosure_requests == 1
    assert loop.rounds[1].disclosure_requests == 0


def test_round_two_revises_rather_than_restarting(harness: Harness) -> None:
    _to_review(harness)
    called = harness.prompts_called()

    assert "demo-p4-x-p4-revise-v2" in called
    assert "demo-p5-x-p5-revise-v2" in called
    # Round 2 re-runs only the audit prompts, not the whole phase.
    assert "demo-p4-16-v2" not in called
    assert "demo-p5-22-v2" not in called
    assert "demo-p4-17-v2" in called


def test_functional_language_flag_triggers_prompt_26(harness: Harness) -> None:
    _to_review(harness)
    assert "demo-p5-26-v2" in harness.prompts_called()


def test_description_and_claims_are_written_back(harness: Harness) -> None:
    _to_review(harness)
    description = harness.dossier.versions("P4")[-1].path / "description.md"
    assert "[0001]" in description.read_text(encoding="utf-8")
    assert "## Deliverable" not in description.read_text(encoding="utf-8")

    registry = json.loads(harness.dossier.registry_path.read_text(encoding="utf-8"))
    assert {entry["numeral"] for entry in registry["entries"]} >= {"10", "30", "60"}
    assert harness.dossier.crosscheck_path.is_file()


# ---------------------------------------------------------------------------
# review, formal fix and the loop-back
# ---------------------------------------------------------------------------


def test_review_package_is_built_and_is_blind(harness: Harness) -> None:
    _to_review(harness)
    review = harness.dossier.versions("REVIEW")[0]
    package_dir = review.path / "package"

    assert (package_dir / "MANIFEST.json").is_file()
    for path in package_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "## Rationale" not in text
        assert not text.startswith("---")

    prompt = (harness.dossier.runs_dir / "demo-review-32-v1-a0" / "prompt.md").read_text(
        encoding="utf-8"
    )
    assert "## Rationale" not in prompt
    assert "04-specification" not in prompt


def test_formal_corrections_are_fixed_and_rechecked_without_a_cycle(
    harness: Harness,
) -> None:
    _to_review(harness)
    called = harness.prompts_called()
    state = harness.state

    assert "demo-p5-x-formal-fix-claims-1-v3" in called
    assert "demo-review-37-recheck-v1" in called
    assert state.loopback.formal_fix_passes == 1
    # The formal pass has its own counter; it never spends a category's budget.
    assert state.loopback.cycles["formal"] == 0
    corrected = harness.dossier.version("P5", 3)
    assert corrected.read_version_json()["trigger"] == "formal-fix"


def test_a_critical_novelty_finding_loops_back_through_phase_3(
    harness: Harness,
) -> None:
    reason = _to_review(harness)

    # The loop-back re-enters Phase 3 and stops for a Gate 2 delta re-confirm.
    assert reason is StopReason.GATE
    state = harness.state
    assert state.gate("2").status == "pending"
    assert state.loopback.cycles["novelty"] == 1
    assert "demo-p3-x-loopback-p3-v2" in harness.prompts_called()
    event = state.loopback.history[-1]
    assert event.route == ["x-loopback-p3", "x-p4-revise", "x-p5-revise"]
    assert event.done == ["x-loopback-p3"]


def test_the_loop_back_completes_and_the_re_review_is_clean(harness: Harness) -> None:
    _to_review(harness)
    harness.decide("2", "accept")
    reason = harness.run(until="review")

    called = harness.prompts_called()
    assert "demo-p4-x-p4-revise-v3" in called
    assert "demo-p5-x-p5-revise-v4" in called
    assert "demo-review-32-v2" in called
    assert reason is StopReason.UNTIL

    state = harness.state
    assert len(state.review.versions) == 2
    assert state.review.versions[-1].open_categories == []


# ---------------------------------------------------------------------------
# packaging and Gate 3
# ---------------------------------------------------------------------------


def _to_gate3(harness: Harness) -> StopReason:
    _to_review(harness)
    harness.decide("2", "accept")
    return harness.run()


def test_the_clean_review_produces_the_final_package(harness: Harness) -> None:
    reason = _to_gate3(harness)

    assert reason is StopReason.GATE
    state = harness.state
    assert state.gate("3").status == "pending"
    assert state.status == "awaiting_gate"

    called = harness.prompts_called()
    for expected in (
        "demo-package-39a-v1",
        "demo-review-39-v2",
        "demo-review-gate14-v2",
        "demo-review-40-v2",
        "demo-package-x-package-memo-v1",
    ):
        assert expected in called

    package_dir = harness.dossier.latest("PACKAGE").path
    names = {path.name for path in package_dir.iterdir()}
    assert names >= {
        "abstract.md",
        "description.md",
        "figures.md",
        "claims.md",
        "support-matrix.md",
        "priority-matrix.md",
        "filing-checklist.md",
        "gate-14.md",
        "residual-findings.md",
        "cover-memo.md",
        "MANIFEST.json",
    }


def test_the_abstract_is_written_from_finished_text_only(harness: Harness) -> None:
    _to_gate3(harness)
    prompt = (harness.dossier.runs_dir / "demo-package-39a-v1-a0" / "prompt.md").read_text(
        encoding="utf-8"
    )
    assert "package/description.md" in prompt
    assert "package/claims.md" in prompt
    assert "01-disclosure" not in prompt


def test_only_a_human_may_close_gate_3(harness: Harness) -> None:
    _to_gate3(harness)
    from ecosystem.gates import GateRefused

    with pytest.raises(GateRefused):
        harness.decide("3", "accept", by="ip-reviewer")

    harness.decide("3", "accept", by="counsel")
    state = harness.state
    assert state.status == "complete"
    assert state.gate("3").decided_by == "counsel"


def test_the_whole_run_is_replayable_and_stops_being_busy(harness: Harness) -> None:
    _to_gate3(harness)
    harness.decide("3", "accept", by="counsel")
    calls_before = len(harness.transport.calls)

    assert harness.run() is StopReason.DONE
    assert len(harness.transport.calls) == calls_before
    assert harness.state.status == "complete"


def test_the_event_log_tells_the_whole_story(harness: Harness) -> None:
    _to_gate3(harness)
    events = [
        json.loads(line)
        for line in (harness.dossier.log_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kinds = {event["event"] for event in events}
    assert {
        "run_started",
        "phase_started",
        "call_started",
        "call_finished",
        "output_written",
        "gate_requested",
        "gate_decided",
        "inner_loop_round",
        "loopback_routed",
        "package_built",
        "run_stopped",
    } <= kinds


def test_a_resolved_error_is_not_reported_as_current(harness: Harness) -> None:
    """A stale `last_error` would have the human debugging a solved problem."""
    from ecosystem.state import LastError

    state = harness.state
    state.last_error = LastError(kind="AssertionError", message="no replay fixture")
    save_state(state, harness.dossier.state_path)

    harness.run()
    assert harness.state.last_error is None
