"""The deterministic claim-support checker: anchors, statuses, dependencies.

The checker is a heuristic, so these tests pin the *behaviour a reader relies
on* - a verbatim limitation is `explicit`, a reworded one is `likely` with the
paragraph it matched, and a limitation the description never mentions is
`unsupported` - rather than exact scores, which are free to move.
"""

from __future__ import annotations

import json

from ecosystem.support_check import (
    SupportReport,
    check_support,
    parse_claims,
    parse_paragraphs,
    split_limitations,
)
from tests.replay_builder import CLAIMS, DESCRIPTION

DESCRIPTION_SIMPLE = """[0001] This disclosure relates to a coffee grinder.

[0002] A grinder housing 10 supports a burr set 20. The burr set 20 is driven by
a motor 30 through a reduction gear 40.

[0003] The reduction gear 40 has a ratio between 3:1 and 7:1, which keeps the
burr temperature below 40 degrees Celsius during a fifty gram dose.
"""

CLAIMS_SIMPLE = """1. A coffee grinder comprising: a grinder housing 10; a burr
set 20 supported by the grinder housing 10; and a motor 30 driving the burr set
20 through a reduction gear 40.

2. The coffee grinder of claim 1, wherein the reduction gear 40 has a ratio
between 3:1 and 7:1.

3. The coffee grinder of claim 1, further comprising a wireless telemetry module
that uploads grind profiles to a remote server.
"""


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_paragraph_ids_are_taken_from_the_text() -> None:
    paragraphs = parse_paragraphs(DESCRIPTION_SIMPLE)
    assert [p.id for p in paragraphs] == ["[0001]", "[0002]", "[0003]"]
    assert paragraphs[1].text.startswith("A grinder housing 10")


def test_unnumbered_paragraphs_fall_back_to_pilcrow_ids() -> None:
    paragraphs = parse_paragraphs("First paragraph.\n\nSecond paragraph.")
    assert [p.id for p in paragraphs] == ["¶1", "¶2"]


def test_claims_are_numbered_from_the_text_not_counted() -> None:
    claims = parse_claims("3. A widget.\n\n4. The widget of claim 3, wherein it is blue.")
    assert [claim.number for claim in claims] == [3, 4]
    assert claims[0].independent
    assert claims[1].depends_on == [3]


def test_limitations_split_on_semicolons_wherein_and_lists() -> None:
    limitations = split_limitations(
        "A widget comprising: a body; a lid attached to the body, wherein the lid "
        "is transparent, and a hinge joining them"
    )
    assert limitations[0] == "A widget"
    assert "a body" in limitations
    assert any(item.startswith("the lid is transparent") for item in limitations)
    assert any(item.startswith("a hinge") for item in limitations)


def test_a_method_preamble_does_not_keep_its_echo() -> None:
    """`A method of X, the method comprising:` must not leave ", the method" behind."""
    limitations = split_limitations(
        "A method of purging a valve core, the method comprising: applying a load"
    )
    assert limitations == ["A method of purging a valve core", "applying a load"]
    # A genuine two-element list is left alone.
    assert split_limitations("A widget comprising: a body, the lid") == [
        "A widget",
        "a body, the lid",
    ]


def test_a_dependency_preamble_is_not_a_limitation() -> None:
    limitations = split_limitations(
        "The coffee grinder of claim 1, wherein the motor 30 is brushless"
    )
    assert limitations == ["the motor 30 is brushless"]


# ---------------------------------------------------------------------------
# statuses
# ---------------------------------------------------------------------------


def test_a_verbatim_limitation_is_explicit_and_cites_its_paragraph() -> None:
    """Claim 2 recites the ratio in the description's own words."""
    report = check_support(DESCRIPTION_SIMPLE, CLAIMS_SIMPLE)
    limitation = next(item for item in report.limitations if "ratio" in item.text)
    assert limitation.status == "explicit"
    assert limitation.anchor is not None
    assert limitation.anchor.method == "exact"
    assert limitation.anchor.paragraph_id == "[0003]"


def test_a_reworded_limitation_is_likely_with_the_paragraph_that_covers_it() -> None:
    """The description says the housing supports the burrs; the claim inverts it."""
    report = check_support(DESCRIPTION_SIMPLE, CLAIMS_SIMPLE)
    limitation = next(
        item for item in report.limitations if item.text.startswith("a burr set 20 supported")
    )
    assert limitation.status == "likely"
    assert limitation.anchor is not None
    assert limitation.anchor.method in {"normalised", "overlap"}
    assert limitation.anchor.paragraph_id == "[0002]"
    assert limitation.anchor.score >= 0.6


def test_a_limitation_the_description_never_mentions_is_unsupported() -> None:
    report = check_support(DESCRIPTION_SIMPLE, CLAIMS_SIMPLE)
    limitation = next(item for item in report.limitations if "telemetry" in item.text)
    assert limitation.status == "unsupported"
    assert limitation.anchor is None
    assert report.unsupported_count == 1


def test_ok_only_reflects_the_independent_claims() -> None:
    """The unsupported limitation above is in dependent claim 3, so `ok` holds."""
    report = check_support(DESCRIPTION_SIMPLE, CLAIMS_SIMPLE)
    assert report.ok is True

    broken = CLAIMS_SIMPLE.replace(
        "3. The coffee grinder of claim 1, further comprising a wireless telemetry module",
        "3. A grinding method comprising: uploading a wireless telemetry module",
    )
    assert check_support(DESCRIPTION_SIMPLE, broken).ok is False


def test_support_can_be_spread_over_two_adjacent_paragraphs() -> None:
    description = (
        "[0010] A pump body 12 carries a diaphragm 14.\n\n"
        "[0011] The diaphragm 14 flexes under pressure so that fluid is displaced "
        "towards the outlet."
    )
    claims = (
        "1. A pump comprising: a pump body 12; and a diaphragm 14 carried by the "
        "pump body 12 and flexing under pressure to displace fluid."
    )
    report = check_support(description, claims)
    anchored = [item.anchor.paragraph_id for item in report.limitations if item.anchor]
    assert "[0010]+[0011]" in anchored


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------


def test_a_forward_dependency_is_reported() -> None:
    report = check_support(
        DESCRIPTION_SIMPLE, "1. A widget.\n\n2. The widget of claim 3.\n\n3. The widget of claim 1."
    )
    assert report.dependency_check.ok is False
    issue = report.dependency_check.issues[0]
    assert (issue.claim, issue.refers_to) == (2, 3)
    assert "lower-numbered" in issue.problem


def test_a_dependency_on_a_claim_that_does_not_exist_is_reported() -> None:
    report = check_support(DESCRIPTION_SIMPLE, "1. A widget.\n\n2. The widget of claim 9.")
    assert report.dependency_check.ok is False
    assert report.dependency_check.issues[0].problem.endswith("does not exist in this claim set")


def test_a_well_formed_claim_set_has_no_dependency_issues() -> None:
    assert check_support(DESCRIPTION_SIMPLE, CLAIMS_SIMPLE).dependency_check.ok is True


# ---------------------------------------------------------------------------
# the realistic sample and the outputs
# ---------------------------------------------------------------------------


def test_the_five_claim_sample_parses_into_two_independent_claims() -> None:
    report = check_support(DESCRIPTION, CLAIMS)
    assert [summary.claim for summary in report.claims] == [1, 2, 3, 4, 5]
    assert [s.claim for s in report.claims if s.independent] == [1, 5]
    assert report.dependency_check.ok is True
    assert report.paragraphs == 6
    # Every claim contributes at least one limitation, and most are anchored.
    assert all(summary.limitations >= 1 for summary in report.claims)
    anchored = sum(1 for item in report.limitations if item.anchor is not None)
    assert anchored >= len(report.limitations) - 2


def test_the_checker_is_pure() -> None:
    first = check_support(DESCRIPTION, CLAIMS)
    second = check_support(DESCRIPTION, CLAIMS)
    assert first.model_dump() == second.model_dump()


def test_the_markdown_table_has_a_row_per_limitation_and_says_it_is_advisory() -> None:
    report = check_support(DESCRIPTION, CLAIMS)
    markdown = report.to_markdown()
    assert "| claim | limitation | anchor | status |" in markdown
    assert markdown.count("\n| ") >= len(report.limitations)
    assert "Prompt 28" in markdown
    assert "## Dependencies" in markdown


def test_the_report_round_trips_through_json() -> None:
    report = check_support(DESCRIPTION_SIMPLE, CLAIMS_SIMPLE)
    restored = SupportReport.from_json(report.to_json())
    assert restored.model_dump() == report.model_dump()
    assert json.loads(report.to_json())["unsupported_count"] == report.unsupported_count


def test_empty_inputs_do_not_explode() -> None:
    report = check_support("", "")
    assert report.claims == []
    assert report.limitations == []
    assert report.ok is True
