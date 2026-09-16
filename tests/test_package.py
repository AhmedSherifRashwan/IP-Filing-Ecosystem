"""Packages: what the blind reviewer gets, what counsel gets, and what leaks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecosystem.config import Settings
from ecosystem.dossier import Dossier
from ecosystem.package import (
    BlindnessViolation,
    PackageError,
    build_final_package,
    build_review_package,
    write_cover_memo,
)
from ecosystem.state import MatterState, new_state
from tests.conftest import write_version
from tests.replay_builder import CLAIMS, DESCRIPTION, REGISTRY_TABLE, review_reply

FIGURE_OUTPUT_20A = f"""## Deliverable

### Figure Instructions

Figure 2: section through the core body 20 showing the seat ring 30.

### Reference-Numeral Registry

{REGISTRY_TABLE}

## Rationale

Three figures are the minimum that supports the claims.

## Assumptions

- A1: none.

## Open Questions

- Q1: none.
"""

FIGURE_OUTPUT_20 = """## Deliverable

### Figure Plan

Figure 1 is an exterior view; Figure 2 a section; Figure 3 the purge gap 60 open.

## Rationale

Secret drafting reasoning.

## Assumptions

- A1: none.

## Open Questions

- Q1: none.
"""


@pytest.fixture
def state(dossier: Dossier) -> MatterState:
    return new_state(dossier.slug, "Demo matter", ["US", "EP"])


def _drafted(dossier: Dossier, state: MatterState) -> None:
    p4 = write_version(
        dossier,
        "P4",
        {"20": FIGURE_OUTPUT_20, "20a": FIGURE_OUTPUT_20A},
        extra_files={"description.md": DESCRIPTION},
    )
    state.phase("P4").current_version = p4
    p5 = write_version(dossier, "P5", {"25": FIGURE_OUTPUT_20}, extra_files={"claims.md": CLAIMS})
    state.phase("P5").current_version = p5
    dossier.write_file(dossier.registry_path, '{"entries": [], "source": "20a"}')


def _review_version(dossier: Dossier, state: MatterState) -> int:
    version = dossier.new_version("REVIEW", "initial")
    state.phase("REVIEW").current_version = version.v
    return version.v


# ---------------------------------------------------------------------------
# review package
# ---------------------------------------------------------------------------


def test_review_package_contains_finished_text_only(
    dossier: Dossier, state: MatterState
) -> None:
    _drafted(dossier, state)
    version = _review_version(dossier, state)
    package_dir = build_review_package(dossier, state, version)

    names = {path.name for path in package_dir.iterdir()}
    assert names == {"description.md", "figures.md", "claims.md", "registry.json", "MANIFEST.json"}
    assert "[0001]" in (package_dir / "description.md").read_text(encoding="utf-8")
    assert "1. A valve core" in (package_dir / "claims.md").read_text(encoding="utf-8")


def test_figures_are_the_20a_instructions_plus_the_20_plan(
    dossier: Dossier, state: MatterState
) -> None:
    _drafted(dossier, state)
    figures = (
        build_review_package(dossier, state, _review_version(dossier, state)) / "figures.md"
    ).read_text(encoding="utf-8")

    assert "## Figure Instructions" in figures
    assert "section through the core body 20" in figures
    assert "## Figure Plan" in figures
    assert "Secret drafting reasoning" not in figures


def test_manifest_records_source_and_sha256(dossier: Dossier, state: MatterState) -> None:
    _drafted(dossier, state)
    package_dir = build_review_package(dossier, state, _review_version(dossier, state))
    manifest = json.loads((package_dir / "MANIFEST.json").read_text(encoding="utf-8"))

    names = [member["name"] for member in manifest["members"]]
    assert names == ["description.md", "figures.md", "claims.md", "registry.json"]
    for member in manifest["members"]:
        assert len(member["sha256"]) == 64
        assert member["source"]
    # Blindness check from the plan's verification section.
    assert all("01-disclosure" not in member["source"] for member in manifest["members"])


def test_rationale_in_the_description_is_a_blindness_violation(
    dossier: Dossier, state: MatterState
) -> None:
    _drafted(dossier, state)
    latest = dossier.versions("P4")[-1]
    (latest.path / "description.md").write_text(
        DESCRIPTION + "\n\n## Rationale\n\nBecause the claims needed it.", encoding="utf-8"
    )
    with pytest.raises(BlindnessViolation, match="Rationale"):
        build_review_package(dossier, state, _review_version(dossier, state))


def test_envelope_in_the_claims_is_a_blindness_violation(
    dossier: Dossier, state: MatterState
) -> None:
    _drafted(dossier, state)
    latest = dossier.versions("P5")[-1]
    (latest.path / "claims.md").write_text(
        CLAIMS + '\n\n```json\n{"prompt": "25"}\n```', encoding="utf-8"
    )
    with pytest.raises(BlindnessViolation, match="envelope"):
        build_review_package(dossier, state, _review_version(dossier, state))


def test_frontmatter_in_a_member_is_a_blindness_violation(
    dossier: Dossier, state: MatterState
) -> None:
    _drafted(dossier, state)
    latest = dossier.versions("P4")[-1]
    (latest.path / "description.md").write_text(
        "---\nprompt: x-p4-compose-description\n---\n\n" + DESCRIPTION, encoding="utf-8"
    )
    with pytest.raises(BlindnessViolation, match="frontmatter"):
        build_review_package(dossier, state, _review_version(dossier, state))


def test_packaging_without_a_registry_is_a_precondition_failure(
    dossier: Dossier, state: MatterState
) -> None:
    _drafted(dossier, state)
    dossier.registry_path.unlink()
    with pytest.raises(PackageError, match="20a"):
        build_review_package(dossier, state, _review_version(dossier, state))


def test_packaging_before_the_inner_loop_is_a_precondition_failure(
    dossier: Dossier, state: MatterState
) -> None:
    with pytest.raises(PackageError, match="description.md"):
        build_review_package(dossier, state, _review_version(dossier, state))


def test_an_abstract_is_carried_into_the_next_review_round(
    dossier: Dossier, state: MatterState
) -> None:
    _drafted(dossier, state)
    first = build_review_package(dossier, state, _review_version(dossier, state))
    (first / "abstract.md").write_text("A valve core with a seat ring.", encoding="utf-8")
    dossier.complete_version("REVIEW", state.phase("REVIEW").current_version)

    second = build_review_package(dossier, state, _review_version(dossier, state))
    assert (second / "abstract.md").read_text(encoding="utf-8").startswith("A valve core")


# ---------------------------------------------------------------------------
# final package
# ---------------------------------------------------------------------------


def _reviewed(dossier: Dossier, state: MatterState) -> int:
    _drafted(dossier, state)
    review = dossier.new_version("REVIEW", "initial")
    state.phase("REVIEW").current_version = review.v
    package_dir = build_review_package(dossier, state, review.v)
    (package_dir / "abstract.md").write_text("A valve core with a seat ring.", encoding="utf-8")

    for prompt_id in ("28", "31", "39"):
        dossier.write_output(
            "REVIEW",
            review.v,
            prompt_id,
            "review",
            review_reply(prompt_id, ["Matrix"]).split("```json")[0],
            {"agent": "ip-reviewer"},
        )
    (review.path / "gate14.envelope.json").write_text(
        json.dumps(
            {
                "prompt": "gate14",
                "gate14": [
                    {"q": q, "answer": "yes" if q != 7 else "no", "evidence": f"q{q}"}
                    for q in range(1, 15)
                ],
            }
        ),
        encoding="utf-8",
    )
    (review.path / "findings.json").write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "id": "F-32-1",
                        "source_prompt": "32",
                        "category": "novelty",
                        "severity": "medium",
                        "title": "Dependent claim 3 is narrow",
                        "claim_refs": ["3"],
                        "spec_refs": [],
                        "attack": "Nobody would infringe it.",
                        "least_narrowing_action": "Leave it as a fallback.",
                        "requires_new_disclosure": False,
                        "suggested_target": "in-place",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    dossier.complete_version("REVIEW", review.v)
    return review.v


def test_final_package_has_everything_counsel_needs(
    dossier: Dossier, state: MatterState
) -> None:
    _reviewed(dossier, state)
    version = dossier.new_version("PACKAGE", "initial")
    state.phase("PACKAGE").current_version = version.v

    target = build_final_package(dossier, state, version.v)
    names = {path.name for path in target.iterdir()}
    assert names >= {
        "abstract.md",
        "description.md",
        "figures.md",
        "claims.md",
        "registry.json",
        "support-matrix.md",
        "priority-matrix.md",
        "filing-checklist.md",
        "gate-14.md",
        "residual-findings.md",
        "cover-memo.md",
        "MANIFEST.json",
    }


def test_gate14_answers_are_reproduced_from_the_envelope(
    dossier: Dossier, state: MatterState
) -> None:
    _reviewed(dossier, state)
    version = dossier.new_version("PACKAGE", "initial")
    target = build_final_package(dossier, state, version.v)

    gate14 = (target / "gate-14.md").read_text(encoding="utf-8")
    assert "| 7 | no |" in gate14
    assert "1 of 14 questions are not a clear `yes`" in gate14


def test_residual_findings_report_medium_and_low(
    dossier: Dossier, state: MatterState
) -> None:
    _reviewed(dossier, state)
    version = dossier.new_version("PACKAGE", "initial")
    target = build_final_package(dossier, state, version.v)

    residual = (target / "residual-findings.md").read_text(encoding="utf-8")
    assert "Dependent claim 3 is narrow" in residual
    assert "Reported, not routed (medium/low)" in residual


def test_the_fallback_memo_never_implies_clearance(
    dossier: Dossier, state: MatterState
) -> None:
    _reviewed(dossier, state)
    version = dossier.new_version("PACKAGE", "initial")
    target = build_final_package(dossier, state, version.v)

    memo = (target / "cover-memo.md").read_text(encoding="utf-8")
    assert "No agent in this system may authorise filing" in memo


def test_cover_memo_is_replaced_and_the_manifest_follows(
    dossier: Dossier, state: MatterState
) -> None:
    _reviewed(dossier, state)
    version = dossier.new_version("PACKAGE", "initial")
    target = build_final_package(dossier, state, version.v)

    write_cover_memo(dossier, target, "# Cover memo\n\nWritten by the orchestrator.")
    memo = (target / "cover-memo.md").read_text(encoding="utf-8")
    assert "Written by the orchestrator." in memo
    # The required sentence is restored even if the model dropped it.
    assert "No agent in this system may authorise filing" in memo

    manifest = json.loads((target / "MANIFEST.json").read_text(encoding="utf-8"))
    entry = next(m for m in manifest["members"] if m["name"] == "cover-memo.md")
    assert entry["source"] == "x-package-memo"


def test_final_package_without_a_review_refuses(
    dossier: Dossier, state: MatterState, settings: Settings
) -> None:
    version = dossier.new_version("PACKAGE", "initial")
    with pytest.raises(PackageError):
        build_final_package(dossier, state, version.v)
    assert isinstance(Path(dossier.root), Path)
