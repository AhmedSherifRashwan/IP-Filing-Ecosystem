from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecosystem.contracts import DraftingEnvelope
from ecosystem.terminology import LockedTable, append_drift, check_output, parse_locked

TABLE = """# Terminology

Some prose first.

| Preferred term | Synonyms | Broader genus | Narrower species | Functional | Avoid | Define? |
|---|---|---|---|---|---|---|
| fastening element | clip, clamp | coupling member | spring clip | holds parts | screw; bolt | y |
| control unit | controller | processing device | microcontroller | runs it | CPU | no |
"""

SHUFFLED = """
| Avoid | Synonym(s) | Preferred | Genus |
|---|---|---|---|
| screw | clip / clamp | fastening element | coupling member |
"""


def table() -> LockedTable:
    return parse_locked(TABLE)


def envelope(**kwargs) -> DraftingEnvelope:
    return DraftingEnvelope.model_validate({"prompt": "22", "status": "complete", **kwargs})


def test_parse_locked_maps_columns_and_splits_lists() -> None:
    parsed = table()

    assert len(parsed) == 2
    row = parsed.rows[0]
    assert row.preferred == "fastening element"
    assert row.synonyms == ["clip", "clamp"]
    assert row.genus == ["coupling member"]
    assert row.species == ["spring clip"]
    assert row.functional == "holds parts"
    assert row.avoid == ["screw", "bolt"]
    assert row.needs_definition is True
    assert parsed.rows[1].needs_definition is False


def test_parse_locked_handles_shuffled_columns_and_missing_ones() -> None:
    parsed = parse_locked(SHUFFLED)

    row = parsed.rows[0]
    assert row.preferred == "fastening element"
    assert row.synonyms == ["clip", "clamp"]
    assert row.avoid == ["screw"]
    assert row.species == []
    assert row.functional == ""


def test_parse_locked_rejects_text_without_a_preferred_column() -> None:
    with pytest.raises(ValueError, match="no terminology table"):
        parse_locked("| A | B |\n|---|---|\n| 1 | 2 |")
    with pytest.raises(ValueError, match="no usable rows"):
        parse_locked("| Preferred |\n|---|\n| |")


def test_json_round_trip() -> None:
    parsed = parse_locked(TABLE, source_sha256="abc")
    restored = LockedTable.from_json(parsed.to_json())

    assert restored.source_sha256 == "abc"
    assert restored.rows[0].avoid == ["screw", "bolt"]
    assert LockedTable.from_json(json.loads(parsed.to_json())).rows[1].preferred == "control unit"


def test_avoid_hit_is_a_violation_for_drafting_and_a_warning_for_analysis() -> None:
    text = "The fastening element is secured by a screw at the rear."

    strict = check_output(text, envelope(), table(), "drafting")
    assert [v.rule for v in strict.violations] == ["avoid-hit"]
    assert strict.violations[0].term == "screw"
    assert strict.violations[0].row == "fastening element"
    assert "secured by a screw" in strict.violations[0].context
    assert strict.blocked

    soft = check_output(text, envelope(), table(), "analysis")
    assert soft.violations == []
    assert [w.rule for w in soft.warnings] == ["avoid-hit"]
    assert not soft.blocked


def test_undeclared_synonym_only_bites_when_the_preferred_term_is_absent() -> None:
    used_alone = check_output("The clip engages the rail.", envelope(), table(), "drafting")
    assert [(v.rule, v.term) for v in used_alone.violations] == [("undeclared-synonym", "clip")]

    used_together = check_output(
        "The fastening element (clip) engages the rail.", envelope(), table(), "drafting"
    )
    assert used_together.violations == []


def test_declared_synonym_is_drift_not_a_violation() -> None:
    declared = envelope(
        new_terms=[{"term": "clip", "maps_to": "fastening element", "reason": "US practice"}]
    )

    report = check_output("The clip engages the rail.", declared, table(), "drafting")

    assert report.violations == []
    assert [d.term for d in report.drift] == ["clip"]
    assert report.drift[0].maps_to == "fastening element"


def test_new_term_without_a_reason_is_a_violation() -> None:
    report = check_output(
        "The fastening element engages the rail.",
        envelope(new_terms=[{"term": "rail guide", "maps_to": None, "reason": "  "}]),
        table(),
        "drafting",
    )

    assert [(v.rule, v.term) for v in report.violations] == [
        ("new-term-without-reason", "rail guide")
    ]
    assert report.drift == []


def test_matching_is_word_bounded_and_case_insensitive() -> None:
    clean = check_output(
        "The fastening element uses screwdriver-free assembly.", envelope(), table(), "drafting"
    )
    assert clean.violations == []

    upper = check_output("The FASTENING ELEMENT uses a Screw.", envelope(), table(), "drafting")
    assert [v.term for v in upper.violations] == ["screw"]


def test_no_envelope_still_checks_the_text() -> None:
    report = check_output("A screw holds it.", None, table(), "drafting")
    assert [v.rule for v in report.violations] == ["avoid-hit"]


def test_append_drift_is_append_only(tmp_path: Path) -> None:
    path = tmp_path / "terminology" / "drift.jsonl"
    declared = envelope(new_terms=[{"term": "clip", "maps_to": "fastening element", "reason": "x"}])
    report = check_output("The clip engages.", declared, table(), "drafting", prompt_id="22")

    assert append_drift(path, report.drift) == 1
    assert append_drift(path, report.drift) == 1
    assert append_drift(path, []) == 0

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["term"] == "clip"
    assert record["prompt"] == "22"
    assert record["at"]
