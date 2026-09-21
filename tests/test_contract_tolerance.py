"""A readable reply is never rejected for its spelling of the envelope."""

from __future__ import annotations

import json

import pytest

from ecosystem.contracts import ContractError, coerce_envelope, parse_reply, split_sections

REVIEW_MD = """## Findings

### Priority-Support Matrix

No priority document was supplied.

### F-31-1 [critical] [support] Priority unassessable

## Corrections

None.

## Assumptions

- A1 The package is the only disclosure.

## Open Questions

- Q1 Is there a priority document?
"""


def test_string_assumptions_and_missing_text_keys_are_repaired() -> None:
    env = {
        "prompt": "31",
        "status": "complete",
        "assumptions": ["The package is complete.", {"id": "A2", "assumption": "No priority doc."}],
        "open_questions": [{"question": "Is there a priority document?"}],
        "findings": [
            {
                "title": "Unassessable",
                "severity": "Major",
                "category": "priority",
                "claim_refs": [1, 2],
                "attack": "x",
                "least_narrowing_action": "y",
            }
        ],
        "corrections": [],
        "gate14": [],
    }
    out, warnings = coerce_envelope(env, "review")
    assert out["assumptions"][0] == {
        "text": "The package is complete.",
        "id": "A1",
        "blocking": False,
    }
    assert out["assumptions"][1]["text"] == "No priority doc."
    assert out["open_questions"][0]["text"].startswith("Is there")
    f = out["findings"][0]
    assert f["severity"] == "high" and f["category"] == "support" and f["claim_refs"] == ["1", "2"]
    assert f["suggested_target"] == "in-place" and f["id"] == "F-31-1"
    assert warnings  # the repairs are reported, not hidden


def test_review_reply_with_string_assumptions_parses() -> None:
    env = {
        "prompt": "31",
        "status": "complete",
        "assumptions": ["a", "b"],
        "open_questions": ["q"],
        "findings": [],
        "corrections": [],
        "gate14": [],
    }
    parsed = parse_reply(REVIEW_MD + "\n```json\n" + json.dumps(env) + "\n```\n", "review")
    assert parsed.envelope is not None
    assert [a.text for a in parsed.envelope.assumptions] == ["a", "b"]
    assert "assumptions normalised" in " ".join(parsed.warnings)


def test_missing_envelope_is_synthesised_from_sections() -> None:
    md = (
        "## Deliverable\n\nText.\n\n## Rationale\n\nWhy.\n\n"
        "## Assumptions\n\n- One\n- Two\n\n## Open Questions\n\n- Q?\n"
    )
    parsed = parse_reply(md, "drafting")
    assert parsed.envelope is not None and parsed.envelope.status == "partial"
    assert [a.text for a in parsed.envelope.assumptions] == ["One", "Two"]
    assert any("synthesised" in w for w in parsed.warnings)


def test_loose_headings_and_unlabelled_deliverable() -> None:
    md = (
        "### Deliverables:\n\nThe work.\n\n**Rationale**\n\nBecause.\n\n"
        '```json\n{"prompt":"12","status":"complete"}\n```\n'
    )
    parsed = parse_reply(md, "drafting")
    assert parsed.sections["Deliverable"].strip() == "The work."
    assert parsed.sections["Rationale"].strip() == "Because."
    md2 = (
        "Here is the combination matrix.\n\n| a | b |\n|---|---|\n\n"
        '```json\n{"prompt":"14","status":"complete"}\n```\n'
    )
    parsed2 = parse_reply(md2, "drafting")
    assert "combination matrix" in parsed2.sections["Deliverable"]
    assert any("taken as the deliverable" in w for w in parsed2.warnings)


def test_only_an_empty_reply_is_rejected() -> None:
    with pytest.raises(ContractError):
        parse_reply("   ", "drafting")
    assert split_sections("## Deliverable\n\nx\n")["Deliverable"] == "x"
