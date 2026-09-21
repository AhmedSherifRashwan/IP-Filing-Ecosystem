"""Long phase outputs are digested head-first; filing text never is."""

from __future__ import annotations

from ecosystem.prompt_builder import _cappable


def test_cap_applies_to_phase_outputs_but_not_filing_text() -> None:
    assert (
        _cappable("out:P1/01") and _cappable("confirmed_embodiments") and _cappable("findings:all")
    )
    for spec in (
        "intake:disclosure",
        "terminology",
        "out:P4/description",
        "out:P5/claims",
        "package:all",
    ):
        assert not _cappable(spec), spec
