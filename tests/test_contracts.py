from __future__ import annotations

import json

import pytest

from ecosystem.contracts import (
    ContractError,
    Correction,
    DraftingEnvelope,
    ReviewEnvelope,
    deliverable_only,
    extract_json_object,
    parse_reply,
    split_sections,
)

DRAFTING_REPLY = """## Deliverable

### Table

The widget comprises a housing.

## Rationale

Because the genus term is broader.

## Assumptions

- A1: steel is available.

## Open Questions

- Q1: temperature range?

```json
{
  "prompt": "11",
  "status": "complete",
  "assumptions": [{"id": "A1", "text": "steel is available"}],
  "open_questions": [{"id": "Q1", "text": "temperature range?", "blocks": ["P4"]}],
  "new_terms": [{"term": "retention feature", "maps_to": "clip", "reason": "broader genus"}],
  "support_refs": [{"item": "housing", "support": "para 12"}]
}
```
"""

REVIEW_REPLY = """## Findings

One critical objection.

```json
{
  "prompt": "32",
  "status": "complete",
  "findings": [
    {
      "id": "F1",
      "source_prompt": "32",
      "category": "novelty",
      "severity": "critical",
      "title": "Claim 1 reads on D1",
      "claim_refs": ["1"],
      "spec_refs": ["[0021]"],
      "attack": "D1 discloses every limitation",
      "least_narrowing_action": "add the retention feature",
      "requires_new_disclosure": false,
      "suggested_target": "P5"
    }
  ],
  "corrections": [
    {"target": "claims", "location": "claim 3", "from": "said widget", "to": "the widget",
     "reason": "antecedent basis"}
  ],
  "gate14": [{"q": 1, "answer": "yes", "evidence": "para 4"}]
}
```
"""


def test_drafting_reply_parses_body_sections_and_envelope() -> None:
    parsed = parse_reply(DRAFTING_REPLY, "drafting")

    assert isinstance(parsed.envelope, DraftingEnvelope)
    assert parsed.envelope.prompt == "11"
    assert parsed.envelope.assumptions[0].id == "A1"
    assert parsed.envelope.assumptions[0].blocking is False
    assert parsed.envelope.open_questions[0].blocks == ["P4"]
    assert parsed.envelope.new_terms[0].maps_to == "clip"
    assert parsed.envelope.disclosure_requests == []
    assert "```json" not in parsed.body_md
    assert set(parsed.sections) == {"Deliverable", "Rationale", "Assumptions", "Open Questions"}
    assert parsed.deliverable.startswith("### Table")


def test_review_reply_parses_findings_gate14_and_the_from_alias() -> None:
    parsed = parse_reply(REVIEW_REPLY, "review")

    assert isinstance(parsed.envelope, ReviewEnvelope)
    assert parsed.envelope.findings[0].category == "novelty"
    assert parsed.envelope.findings[0].suggested_target == "P5"
    assert parsed.envelope.corrections[0].from_text == "said widget"
    assert parsed.envelope.gate14[0].answer == "yes"
    # The wire name survives a round trip in both directions.
    assert parsed.envelope.corrections[0].model_dump(by_alias=True)["from"] == "said widget"


def test_correction_accepts_either_field_name() -> None:
    by_alias = Correction(target="claims", location="c1", **{"from": "a"}, to="b", reason="typo")
    by_name = Correction(target="claims", location="c1", from_text="a", to="b", reason="typo")
    assert by_alias.from_text == by_name.from_text == "a"


def test_the_last_json_block_wins() -> None:
    reply = (
        "## Deliverable\n\nExample envelope for illustration:\n\n"
        '```json\n{"prompt": "EXAMPLE", "status": "partial"}\n```\n\n'
        "## Rationale\n\nr\n\n## Assumptions\n\na\n\n## Open Questions\n\nq\n\n"
        '```json\n{"prompt": "11", "status": "complete"}\n```\n'
    )

    parsed = parse_reply(reply, "drafting")

    assert parsed.envelope is not None
    assert parsed.envelope.prompt == "11"
    assert "EXAMPLE" in parsed.body_md


def test_missing_sections_raise_a_correctable_contract_error() -> None:
    reply = '## Deliverable\n\nbody\n\n```json\n{"prompt": "11", "status": "complete"}\n```\n'

    with pytest.raises(ContractError) as excinfo:
        parse_reply(reply, "drafting")

    error = excinfo.value
    assert error.missing == ["## Rationale", "## Assumptions", "## Open Questions"]
    assert "`## Rationale` section is missing" in error.message
    assert "`## Open Questions` section is missing" in error.message
    assert "Resend the COMPLETE reply" in error.correction_text()
    assert error.message in error.correction_text()


def test_missing_envelope_and_invalid_envelope_are_distinct_errors() -> None:
    with pytest.raises(ContractError) as missing:
        parse_reply("## Findings\n\nnothing to report\n", "review")
    assert "json envelope" in missing.value.missing

    bad = '## Findings\n\nx\n\n```json\n{"prompt": "32", "status": "nope"}\n```\n'
    with pytest.raises(ContractError) as invalid:
        parse_reply(bad, "review")
    assert "valid envelope" in invalid.value.missing
    assert "status" in invalid.value.message


def test_bad_finding_category_is_rejected() -> None:
    reply = REVIEW_REPLY.replace('"category": "novelty"', '"category": "style"')
    with pytest.raises(ContractError) as excinfo:
        parse_reply(reply, "review")
    assert "category" in excinfo.value.message


def test_prose_kind_needs_no_envelope_or_sections() -> None:
    parsed = parse_reply("Dear Ahmed,\n\nGate 1 is ready.\n", "prose")
    assert parsed.envelope is None
    assert parsed.body_md.startswith("Dear Ahmed")


def test_empty_reply_is_rejected() -> None:
    with pytest.raises(ContractError):
        parse_reply("   \n", "drafting")


def test_split_sections_ignores_headings_inside_code_fences() -> None:
    body = "## Deliverable\n\n```\n## Not a heading\n```\n\n## Rationale\n\nr\n"
    sections = split_sections(body)
    assert set(sections) == {"Deliverable", "Rationale"}
    assert "## Not a heading" in sections["Deliverable"]


def test_deliverable_only_returns_the_deliverable_text() -> None:
    assert deliverable_only(DRAFTING_REPLY).startswith("### Table")
    assert "Rationale" not in deliverable_only(DRAFTING_REPLY)
    assert deliverable_only("no sections") == ""


def test_extract_json_object_falls_back_to_a_bare_object() -> None:
    assert extract_json_object('prefix {"a": 1} suffix') == {"a": 1}
    assert extract_json_object("no json here") is None
    assert extract_json_object("") is None
    assert extract_json_object('```json\n{"a": 1}\n```\n```json\n{"a": 2}\n```')["a"] == 2


def test_envelopes_keep_unexpected_fields() -> None:
    envelope = DraftingEnvelope.model_validate(
        {"prompt": "03", "status": "complete", "inventive_nucleus": ["the clip"]}
    )
    assert json.loads(envelope.model_dump_json())["inventive_nucleus"] == ["the clip"]
