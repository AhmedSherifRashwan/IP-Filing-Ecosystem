from __future__ import annotations

import json

import pytest

from ecosystem.numerals import CrossCheck, Registry, cross_check, parse_registry

REGISTRY_MD = """## Deliverable

### Reference-numeral registry

| Numeral | Component | First figure | Figures |
|---|---|---|---|
| 10 | housing | FIG. 1 | FIG. 1, FIG. 2 |
| 20 | fastening element | FIG. 1 | FIG. 1; FIG. 3 |
| 30 | control unit | FIG. 2 | |
| 40a | sensor array | Fig 3 | 3 and 4 |
"""


def registry() -> Registry:
    return parse_registry(REGISTRY_MD)


def test_parse_registry_reads_numerals_components_and_figures() -> None:
    parsed = registry()

    assert len(parsed) == 4
    assert parsed.by_numeral()["10"].component == "housing"
    assert parsed.by_numeral()["10"].figures == ["1", "2"]
    assert parsed.by_numeral()["20"].figures == ["1", "3"]
    assert parsed.by_numeral()["40a"].first_figure == "3"
    assert parsed.by_numeral()["40a"].figures == ["3", "4"]
    # A numeral listed only as "first figure" still appears in that figure.
    assert parsed.by_numeral()["30"].figures == ["2"]


def test_parse_registry_rejects_a_table_without_numerals() -> None:
    with pytest.raises(ValueError, match="no reference-numeral table"):
        parse_registry("| Component | Figure |\n|---|---|\n| housing | 1 |")
    with pytest.raises(ValueError, match="no usable rows"):
        parse_registry("| Numeral | Component |\n|---|---|\n| n/a | housing |")


def test_json_round_trip() -> None:
    restored = Registry.from_json(registry().to_json())
    assert restored.by_numeral()["20"].component == "fastening element"
    assert Registry.from_json(json.loads(registry().to_json())).entries[0].numeral == "10"

    check = CrossCheck(numerals_in_text_not_in_registry=["99"], ok=False)
    assert CrossCheck.from_json(check.to_json()).ok is False


def test_cross_check_is_clean_when_everything_lines_up() -> None:
    description = "The housing 10 supports the fastening element 20 and the control unit 30."
    claims = "1. A device comprising a housing 10 and a fastening element 20."

    result = cross_check(registry(), description, claims)

    assert result.numerals_in_text_not_in_registry == []
    assert result.registry_numerals_not_in_figures == []
    assert result.claim_components_without_numeral == []
    assert result.ok is True


def test_cross_check_finds_a_numeral_that_is_not_registered() -> None:
    description = "The housing 10 receives a bracket 99 and a spacer 105."

    result = cross_check(registry(), description, "")

    assert result.numerals_in_text_not_in_registry == ["99", "105"]
    assert result.ok is False


def test_cross_check_ignores_figure_claim_and_measurement_references() -> None:
    description = (
        "As shown in FIG. 12 and FIGS. 13-14, claim 11 is supported at paragraph 21. "
        "The temperature is about 250 degrees and step 15 follows. The housing 10 is steel."
    )

    result = cross_check(registry(), description, "")

    assert result.numerals_in_text_not_in_registry == []


def test_cross_check_finds_a_registry_numeral_absent_from_every_figure() -> None:
    parsed = registry()
    parsed.entries[2].figures = []
    parsed.entries[2].first_figure = ""

    result = cross_check(parsed, "The housing 10.", "")

    assert result.registry_numerals_not_in_figures == ["30"]
    assert result.ok is False


def test_cross_check_finds_a_claimed_component_without_its_numeral() -> None:
    claims = "1. A device comprising a housing 10 and a control unit configured to act."

    result = cross_check(registry(), "The housing 10.", claims)

    assert result.claim_components_without_numeral == ["control unit"]
    assert result.ok is False


def test_cross_check_tolerates_empty_documents() -> None:
    result = cross_check(registry(), "", "")
    assert result.numerals_in_text_not_in_registry == []
    assert result.claim_components_without_numeral == []
    assert result.ok is True
