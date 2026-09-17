"""Loop-back engine: aggregation, dedupe, routing, caps, formal separation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ecosystem.config import Settings
from ecosystem.loopback import (
    ROUTES,
    TITLE_MATCH_RATIO,
    aggregate,
    corrections_in,
    counts,
    is_routable,
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


# ---------------------------------------------------------------------------
# two independent reviewers
# ---------------------------------------------------------------------------


def b_envelope(directory: Path, prompt: str, **payload: Any) -> Path:
    """The second reviewer's copy of one review prompt, filed as `<id>b`."""
    return envelope(directory, f"{prompt}b", **payload)


def test_a_finding_both_reviewers_raised_becomes_one_consensus_finding(
    review_dir: Path,
) -> None:
    envelope(review_dir, "32", findings=[finding("32", 1)])
    b_envelope(
        review_dir,
        "32",
        findings=[finding("32", 1, title="Claim 1 reads on the admitted prior art")],
    )

    findings = aggregate(review_dir)
    assert len(findings) == 1
    assert findings[0].consensus is True
    assert findings[0].reviewers == ["ip-reviewer", "ip-reviewer-b"]


def test_two_reviewers_phrasing_the_same_objection_differently_still_agree(
    review_dir: Path,
) -> None:
    """Different models do not write the same sentence. Exact matching would
    report two independent findings where there is one, and the consensus mark
    would then be unreachable in practice."""
    envelope(review_dir, "32", findings=[finding("32", 1, title="Claim 1 is anticipated")])
    b_envelope(
        review_dir, "32", findings=[finding("32", 1, title="Claim 1 is anticipated outright")]
    )
    assert len(aggregate(review_dir)) == 1


def test_different_objections_in_one_category_are_not_forced_together(
    review_dir: Path,
) -> None:
    envelope(review_dir, "32", findings=[finding("32", 1, title="Claim 1 is anticipated")])
    b_envelope(
        review_dir,
        "32",
        findings=[finding("32", 1, title="No technical effect is credible", claims=["4"])],
    )

    findings = aggregate(review_dir)
    assert len(findings) == 2
    assert all(f.consensus is False for f in findings)
    assert sorted(f.reviewers[0] for f in findings) == ["ip-reviewer", "ip-reviewer-b"]


def test_a_consensus_finding_takes_the_more_alarming_severity(review_dir: Path) -> None:
    envelope(review_dir, "32", findings=[finding("32", 1, severity="high")])
    b_envelope(review_dir, "32", findings=[finding("32", 1, severity="critical")])

    findings = aggregate(review_dir)
    assert len(findings) == 1
    assert findings[0].severity == "critical"


def test_each_reviewer_finding_is_matched_at_most_once(review_dir: Path) -> None:
    """Two near-identical A findings must not both claim the same B finding."""
    envelope(
        review_dir,
        "32",
        findings=[
            finding("32", 1, title="Claim 1 is anticipated"),
            finding("32", 2, title="Claim 1 is anticipated again"),
        ],
    )
    b_envelope(review_dir, "32", findings=[finding("32", 1, title="Claim 1 is anticipated")])

    findings = aggregate(review_dir)
    assert [f.consensus for f in findings].count(True) == 1
    assert len(findings) == 2


def test_the_consensus_summary_is_written_beside_the_findings(review_dir: Path) -> None:
    envelope(review_dir, "32", findings=[finding("32", 1)])
    b_envelope(
        review_dir,
        "32",
        findings=[
            finding("32", 1, title="Claim 1 reads on the admitted prior art"),
            finding("32", 2, title="Claim 4 adds nothing", severity="high", claims=["4"]),
        ],
    )
    aggregate(review_dir)

    summary = json.loads((review_dir / "consensus-summary.json").read_text(encoding="utf-8"))
    assert summary["counts"] == {"consensus": 1, "a_only": 0, "b_only": 1}
    assert summary["by_severity"]["critical"]["consensus"] == 1
    assert summary["by_severity"]["high"]["b_only"] == 1
    assert summary["reviewers"] == ["ip-reviewer", "ip-reviewer-b"]


def test_a_recheck_supersedes_both_reviewers_copies(review_dir: Path) -> None:
    """The formal fix changed the text both of them audited."""
    correction = {
        "target": "claims",
        "location": "claim 4",
        "from": "a return spring",
        "to": "the return spring",
        "reason": "antecedent basis",
    }
    envelope(review_dir, "37", corrections=[correction])
    b_envelope(review_dir, "37", corrections=[correction])
    envelope(review_dir, "37r", corrections=[])

    assert corrections_in(review_dir) == []


def test_findings_written_before_the_second_reviewer_existed_still_load(
    review_dir: Path,
) -> None:
    envelope(review_dir, "32", findings=[finding("32", 1)])
    aggregate(review_dir)
    payload = json.loads((review_dir / "findings.json").read_text(encoding="utf-8"))
    for item in payload["findings"]:
        item.pop("reviewers")
        item.pop("consensus")
    (review_dir / "findings.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_findings(review_dir)
    assert [f.id for f in loaded] == ["F-32-1"]
    assert loaded[0].reviewers == []
    assert loaded[0].consensus is False


# ---------------------------------------------------------------------------
# routing on agreement
# ---------------------------------------------------------------------------


def _consensus_finding(**overrides: Any) -> dict[str, Any]:
    item = finding(**overrides)
    item["reviewers"] = ["ip-reviewer", "ip-reviewer-b"]
    item["consensus"] = True
    return item


def _single_finding(who: str = "ip-reviewer", **overrides: Any) -> dict[str, Any]:
    item = finding(**overrides)
    item["reviewers"] = [who]
    item["consensus"] = False
    return item


def test_a_critical_routes_whether_or_not_the_reviewers_agree(
    state: MatterState, settings: Settings
) -> None:
    for who in ("ip-reviewer", "ip-reviewer-b"):
        plan = plan_routes(_findings(_single_finding(who, severity="critical")), state, settings)
        assert plan.categories == ["novelty"]
        assert plan.single_reviewer_highs == []


def test_a_high_routes_only_when_both_reviewers_raised_it(
    state: MatterState, settings: Settings
) -> None:
    agreed = plan_routes(_findings(_consensus_finding(severity="high")), state, settings)
    assert agreed.categories == ["novelty"]
    assert agreed.single_reviewer_highs == []

    alone = plan_routes(_findings(_single_finding(severity="high")), state, settings)
    assert alone.categories == []
    assert alone.clean is True
    assert alone.single_reviewer_highs == ["F-32-1"]


def test_a_carried_high_is_reported_to_the_human(state: MatterState, settings: Settings) -> None:
    findings = _findings(_single_finding(severity="high"))
    assert residual_findings(findings) == []
    assert [f.id for f in residual_findings(findings, settings)] == ["F-32-1"]


def test_without_a_second_reviewer_a_high_routes_as_it_always_did(
    state: MatterState, settings: Settings
) -> None:
    single = settings.model_copy(update={"second_reviewer": False})
    plan = plan_routes(_findings(_single_finding(severity="high")), state, single)
    assert plan.categories == ["novelty"]
    assert plan.single_reviewer_highs == []
    assert residual_findings(_findings(_single_finding(severity="high")), single) == []


def test_is_routable_is_the_whole_rule(settings: Settings) -> None:
    single = settings.model_copy(update={"second_reviewer": False})
    cases = {
        ("critical", False): (True, True),
        ("critical", True): (True, True),
        ("high", True): (True, True),
        ("high", False): (False, True),
        ("medium", True): (False, False),
        ("low", True): (False, False),
    }
    for (severity, consensus), (with_b, without_b) in cases.items():
        item = _findings(finding(severity=severity))[0]
        item.consensus = consensus
        assert is_routable(item, settings) is with_b, (severity, consensus)
        assert is_routable(item, single) is without_b, (severity, consensus)


def test_the_title_threshold_errs_towards_reporting_two_findings(review_dir: Path) -> None:
    """0.6 is a deliberate bias, and this test exists to make the cost visible.

    Two models that re-word an objection with different verbs can fall just
    under it: "Claim 1 reads on a conventional two-rate core" against "Claim 1
    is anticipated by the admitted two-rate core" scores 0.598. They are then
    reported as two single-reviewer findings rather than one consensus finding.

    That is the right way to be wrong. A false merge would do two bad things at
    once: promote one model's lead to "evidence" and delete the other
    reviewer's differently-aimed objection from the record. Two entries on the
    residual list costs counsel a minute's reading. Raise TITLE_MATCH_RATIO to
    merge less, lower it to merge more; do not lower it to flatter the numbers.
    """
    assert TITLE_MATCH_RATIO == 0.6
    envelope(
        review_dir,
        "32",
        findings=[finding("32", 1, title="Claim 1 reads on a conventional two-rate core")],
    )
    b_envelope(
        review_dir,
        "32",
        findings=[finding("32", 1, title="Claim 1 is anticipated by the admitted two-rate core")],
    )

    findings = aggregate(review_dir)
    assert len(findings) == 2
    assert all(f.consensus is False for f in findings)
