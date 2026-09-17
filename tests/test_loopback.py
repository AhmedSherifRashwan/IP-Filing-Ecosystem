"""Loop-back engine: aggregation, dedupe, routing, caps, formal separation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ecosystem.config import Settings
from ecosystem.loopback import (
    ROUTES,
    aggregate,
    corrections_in,
    counts,
    load_findings,
    open_categories,
    plan_routes,
    residual_findings,
)
from ecosystem.state import ForcedDecision, MatterState, new_state


@pytest.fixture
def review_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "06-review" / "v1-2026-01-01"
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def state() -> MatterState:
    return new_state("demo", "Demo", ["US"])


def finding(
    source: str = "32",
    n: int = 1,
    category: str = "novelty",
    severity: str = "critical",
    title: str = "Claim 1 reads on the admitted prior art",
    claims: list[str] | None = None,
    target: str = "P3",
) -> dict[str, Any]:
    return {
        "id": f"F-{source}-{n}",
        "source_prompt": source,
        "category": category,
        "severity": severity,
        "title": title,
        "claim_refs": claims if claims is not None else ["1"],
        "spec_refs": ["[0004]"],
        "attack": "The claim recites nothing the description does not admit is known.",
        "least_narrowing_action": "Add disclosure of the distinguishing mechanism.",
        "requires_new_disclosure": True,
        "suggested_target": target,
    }


def envelope(directory: Path, prompt: str, **payload: Any) -> Path:
    body = {
        "prompt": prompt,
        "status": "complete",
        "findings": [],
        "corrections": [],
        "gate14": [],
        "assumptions": [],
        "open_questions": [],
    }
    body.update(payload)
    path = directory / f"{prompt}.envelope.json"
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def test_aggregate_merges_every_envelope(review_dir: Path) -> None:
    envelope(review_dir, "32", findings=[finding("32", 1), finding("32", 2, title="Second")])
    envelope(review_dir, "33", findings=[finding("33", 1, category="design-around")])

    findings = aggregate(review_dir)
    assert len(findings) == 3
    assert (review_dir / "findings.json").is_file()
    assert {f.source_prompt for f in findings} == {"32", "33"}


def test_the_same_objection_from_two_prompts_is_one_finding(review_dir: Path) -> None:
    envelope(review_dir, "32", findings=[finding("32", 1)])
    envelope(review_dir, "36", findings=[finding("36", 1)])  # same category/claims/title

    findings = aggregate(review_dir)
    assert len(findings) == 1


def test_titles_differing_only_in_case_are_the_same_objection(review_dir: Path) -> None:
    envelope(review_dir, "32", findings=[finding("32", 1, title="Claim 1 Is Anticipated")])
    envelope(review_dir, "33", findings=[finding("33", 1, title="claim 1 is anticipated")])
    assert len(aggregate(review_dir)) == 1


def test_ids_are_renumbered_per_source_prompt(review_dir: Path) -> None:
    envelope(
        review_dir,
        "32",
        findings=[
            finding("32", 9, title="B finding", severity="high"),
            finding("32", 4, title="A finding", severity="critical"),
        ],
    )
    findings = aggregate(review_dir)
    assert sorted(f.id for f in findings) == ["F-32-1", "F-32-2"]
    # Critical sorts before high, so the renumbering is severity-ordered.
    assert findings[0].severity == "critical"


def test_malformed_findings_are_skipped_not_fatal(review_dir: Path) -> None:
    envelope(review_dir, "32", findings=[finding("32", 1), {"id": "broken"}])
    assert len(aggregate(review_dir)) == 1


def test_recheck_envelope_supersedes_the_original(review_dir: Path) -> None:
    """After a formal fix, `37r` is the truth and `37` is history."""
    envelope(review_dir, "37", findings=[finding("37", 1, category="formal")])
    envelope(review_dir, "37r", findings=[])
    assert aggregate(review_dir) == []


def test_corrections_are_collected_and_deduped(review_dir: Path) -> None:
    correction = {
        "target": "claims",
        "location": "claim 4",
        "from": "a return spring",
        "to": "the return spring",
        "reason": "antecedent basis",
    }
    envelope(review_dir, "37", corrections=[correction])
    envelope(review_dir, "40a", corrections=[correction])

    found = corrections_in(review_dir)
    assert len(found) == 1
    assert found[0].from_text == "a return spring"
    assert found[0].model_dump(by_alias=True)["from"] == "a return spring"


def test_load_findings_round_trips(review_dir: Path) -> None:
    envelope(review_dir, "32", findings=[finding("32", 1)])
    aggregate(review_dir)
    assert [f.id for f in load_findings(review_dir)] == ["F-32-1"]


def test_counts_and_open_categories(review_dir: Path) -> None:
    envelope(
        review_dir,
        "32",
        findings=[
            finding("32", 1, severity="critical"),
            finding("32", 2, category="support", severity="medium", title="Support gap"),
        ],
    )
    findings = aggregate(review_dir)
    assert counts(findings) == {"critical": 1, "high": 0, "medium": 1, "low": 0}
    assert open_categories(findings) == ["novelty"]
    assert [f.id for f in residual_findings(findings)] == ["F-32-2"]


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------


def _findings(*raw: dict[str, Any]):
    from ecosystem.contracts import Finding

    return [Finding.model_validate(item) for item in raw]


def test_medium_and_low_never_route(state: MatterState, settings: Settings) -> None:
    plan = plan_routes(
        _findings(finding(severity="medium"), finding(severity="low")), state, settings
    )
    assert plan.clean
    assert plan.steps == []


def test_novelty_routes_the_full_pass_in_pipeline_order(
    state: MatterState, settings: Settings
) -> None:
    plan = plan_routes(_findings(finding(category="novelty")), state, settings)
    assert plan.steps == ["x-loopback-p3", "x-p4-revise", "x-p5-revise"]
    assert plan.gate2_reconfirm is True
    assert plan.categories == ["novelty"]


def test_support_adds_disclosure_and_never_narrows(state: MatterState, settings: Settings) -> None:
    plan = plan_routes(
        _findings(finding(category="support", title="No basis for claim 3", target="P4")),
        state,
        settings,
    )
    assert plan.steps == ["x-p4-revise"]
    assert plan.gate2_reconfirm is False


def test_support_narrowing_suggestions_are_surfaced_not_applied(
    state: MatterState, settings: Settings
) -> None:
    item = finding(category="support", title="No basis", target="P4")
    item["least_narrowing_action"] = "Narrow claim 1 to the disclosed range."
    plan = plan_routes(_findings(item), state, settings)
    assert plan.steps == ["x-p4-revise"]
    assert plan.narrowing_notes and "Narrow claim 1" in plan.narrowing_notes[0]


def test_enablement_reaches_the_claims_only_when_it_names_them(
    state: MatterState, settings: Settings
) -> None:
    spec_only = finding(category="enablement", target="P4", claims=[])
    assert plan_routes(_findings(spec_only), state, settings).steps == ["x-p4-revise"]

    claim_facing = finding(category="enablement", target="P5")
    assert plan_routes(_findings(claim_facing), state, settings).steps == [
        "x-p4-revise",
        "x-p5-revise",
    ]


def test_categories_are_routed_in_one_combined_pass(state: MatterState, settings: Settings) -> None:
    plan = plan_routes(
        _findings(
            finding(category="novelty"),
            finding(category="support", title="No basis", target="P4"),
        ),
        state,
        settings,
    )
    assert plan.steps == ["x-loopback-p3", "x-p4-revise", "x-p5-revise"]
    assert plan.categories == ["novelty", "support"]


def test_formal_never_enters_the_combined_pass(state: MatterState, settings: Settings) -> None:
    plan = plan_routes(
        _findings(finding(category="formal", title="Antecedent basis", target="in-place")),
        state,
        settings,
    )
    assert plan.steps == []
    assert plan.formal is True
    assert plan.categories == []
    assert ROUTES["formal"]["counts_against_cap"] is False


def test_formal_stops_at_its_own_cap(state: MatterState, settings: Settings) -> None:
    state.loopback.formal_fix_passes = settings.formal_fix_cap
    plan = plan_routes(
        _findings(finding(category="formal", title="Antecedent basis", target="in-place")),
        state,
        settings,
    )
    assert plan.formal is False
    assert plan.formal_exhausted is True


def test_category_at_its_cap_becomes_a_cap_gate(state: MatterState, settings: Settings) -> None:
    state.loopback.cycles["novelty"] = settings.loopback_cap
    plan = plan_routes(_findings(finding(category="novelty")), state, settings)

    assert plan.capped == ["novelty"]
    assert plan.steps == []
    assert plan.clean is False
    assert plan.finding_ids["novelty"] == ["F-32-1"]


def test_manual_fix_buys_exactly_one_more_cycle(state: MatterState, settings: Settings) -> None:
    state.loopback.cycles["novelty"] = settings.loopback_cap
    state.loopback.forced_decisions.append(
        ForcedDecision(category="novelty", decision="manual-fix", decided_by="ahmed")
    )
    plan = plan_routes(_findings(finding(category="novelty")), state, settings)
    assert plan.capped == []
    assert plan.categories == ["novelty"]

    state.loopback.cycles["novelty"] += 1
    plan = plan_routes(_findings(finding(category="novelty")), state, settings)
    assert plan.capped == ["novelty"]
