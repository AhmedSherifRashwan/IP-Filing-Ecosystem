"""Prompt assembly: order, headings, rationale stripping, budget, transport hint."""

from __future__ import annotations

from pathlib import Path

import pytest

from ecosystem.config import Settings
from ecosystem.dossier import Dossier, InputContext, MissingInput
from ecosystem.playbook import PromptSpec, load_playbook
from ecosystem.prompt_builder import (
    PromptTooLarge,
    assemble,
    contract_fragment_name,
    synthetic_input,
    transport_hint,
)
from ecosystem.state import new_state
from tests.conftest import DRAFTING_BODY, REPO_ROOT, write_version


def _budget(tmp_path: Path, chars: int) -> Settings:
    """Same temp run, smaller prompt budget."""
    return Settings(repo_root=tmp_path, prompts_dir=REPO_ROOT / "prompts", max_prompt_chars=chars)


@pytest.fixture
def playbook(settings: Settings):
    return load_playbook(settings.prompts_path)


def _spec(**overrides) -> PromptSpec:
    data = {
        "id": "t1",
        "title": "Test prompt",
        "phase": "P2",
        "agent": "ip-prior-art",
        "kind": "analysis",
        "inputs": [],
        "optional_inputs": [],
        "deliverable_sections": ["Section"],
        "body": "Do the thing.",
    }
    data.update(overrides)
    return PromptSpec.model_validate(data)


def _assemble(dossier: Dossier, settings: Settings, spec: PromptSpec, **kwargs):
    state = new_state(dossier.slug, "Demo", ["US"])
    return assemble(dossier, state, spec, InputContext(), settings, **kwargs)


# ---------------------------------------------------------------------------
# order and shape
# ---------------------------------------------------------------------------


def test_sections_appear_in_the_contracted_order(dossier: Dossier, settings: Settings) -> None:
    write_version(dossier, "P1", {"01": DRAFTING_BODY})
    result = _assemble(dossier, settings, _spec(inputs=["out:P1/01"]))

    positions = [
        result.text.index("Act as a senior patent drafting team"),
        result.text.index("## Task"),
        result.text.index("## Dossier files"),
        result.text.index("## Output contract"),
    ]
    assert positions == sorted(positions)
    assert "Do the thing." in result.text


def test_each_dossier_file_gets_a_versioned_sha_heading(
    dossier: Dossier, settings: Settings
) -> None:
    v = write_version(dossier, "P1", {"01": DRAFTING_BODY})
    result = _assemble(dossier, settings, _spec(inputs=["out:P1/01"]))

    item = result.inputs[0]
    assert f"### 01-disclosure/v{v}-" in result.text
    assert f"sha256:{item.sha256[:12]}" in result.text
    assert f"(v{v}, sha256:" in result.text


def test_rationale_never_reaches_the_prompt(dossier: Dossier, settings: Settings) -> None:
    write_version(dossier, "P1", {"01": DRAFTING_BODY})
    result = _assemble(dossier, settings, _spec(inputs=["out:P1/01"]))

    assert "Chosen because the alternative narrows scope" not in result.text
    assert "The widget comprises a housing 10" in result.text


def test_contract_fragment_by_kind() -> None:
    assert contract_fragment_name("drafting") == "_contract-drafting"
    assert contract_fragment_name("compose") == "_contract-drafting"
    assert contract_fragment_name("revise") == "_contract-drafting"
    assert contract_fragment_name("analysis") == "_contract-drafting"
    assert contract_fragment_name("review") == "_contract-review"
    assert contract_fragment_name("prose") is None


def test_prose_prompts_get_no_output_contract(dossier: Dossier, settings: Settings) -> None:
    result = _assemble(dossier, settings, _spec(kind="prose", agent="patentcounsel"))
    assert "## Output contract" not in result.text


def test_review_prompts_get_the_review_contract(dossier: Dossier, settings: Settings) -> None:
    result = _assemble(dossier, settings, _spec(kind="review", agent="ip-reviewer"))
    assert "You are reviewing finished text only" in result.text
    assert "findings" in result.text


# ---------------------------------------------------------------------------
# terminology section
# ---------------------------------------------------------------------------


def _lock(dossier: Dossier, text: str = "| Preferred term |\n| --- |\n| seat ring |") -> None:
    dossier.write_file(dossier.locked_terminology_path, text, overwrite=True)


def test_locked_terminology_gets_its_own_section(dossier: Dossier, settings: Settings) -> None:
    _lock(dossier)
    result = _assemble(dossier, settings, _spec(inputs=["terminology"]))

    assert "## Locked terminology" in result.text
    # Exactly one copy: it must not also appear as a dossier file.
    assert result.text.count("| seat ring |") == 1
    assert result.text.index("## Locked terminology") > result.text.index("## Dossier files")


def test_lock_is_embedded_even_when_the_prompt_forgot_to_ask(
    dossier: Dossier, settings: Settings
) -> None:
    _lock(dossier)
    result = _assemble(dossier, settings, _spec(inputs=[]))
    assert "## Locked terminology" in result.text


def test_no_lock_before_gate_1_is_not_an_error_for_p1(dossier: Dossier, settings: Settings) -> None:
    result = _assemble(dossier, settings, _spec(phase="P1", agent="ip-disclosure"))
    assert "## Locked terminology" not in result.text


def test_missing_required_input_raises(dossier: Dossier, settings: Settings) -> None:
    with pytest.raises(MissingInput):
        _assemble(dossier, settings, _spec(inputs=["out:P1/01"]))


def test_missing_optional_input_is_recorded_not_fatal(dossier: Dossier, settings: Settings) -> None:
    result = _assemble(dossier, settings, _spec(optional_inputs=["out:P1/01"]))
    assert result.missing_optional == ["out:P1/01"]


def test_tolerate_missing_records_required_gaps_for_dry_runs(
    dossier: Dossier, settings: Settings
) -> None:
    result = _assemble(dossier, settings, _spec(inputs=["out:P1/01"]), tolerate_missing=True)
    assert result.missing_required == ["out:P1/01"]


def test_synthetic_inputs_lead_the_dossier_files(dossier: Dossier, settings: Settings) -> None:
    write_version(dossier, "P1", {"01": DRAFTING_BODY})
    extra = synthetic_input("gate:1", "gates/gate-1.pending.json", '{"gate": "1"}')
    result = _assemble(dossier, settings, _spec(inputs=["out:P1/01"]), extra_inputs=[extra])
    assert result.text.index("gates/gate-1.pending.json") < result.text.index("01-disclosure/")


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------


def _big(dossier: Dossier, name: str, size: int) -> None:
    (dossier.intake_dir / "data").mkdir(parents=True, exist_ok=True)
    (dossier.intake_dir / "data" / name).write_text("x" * size, encoding="utf-8")


def test_optional_inputs_are_dropped_largest_first(dossier: Dossier, tmp_path: Path) -> None:
    settings = _budget(tmp_path, 12_000)
    dossier.settings = settings
    _big(dossier, "small.md", 2_000)
    _big(dossier, "huge.md", 40_000)

    result = _assemble(
        dossier, settings, _spec(optional_inputs=["intake:data/huge.md", "intake:data/small.md"])
    )
    assert result.dropped_optional == ["intake:data/huge.md"]
    assert result.chars <= 12_000
    assert "small.md" in result.text


def test_over_budget_required_inputs_raise(dossier: Dossier, tmp_path: Path) -> None:
    settings = _budget(tmp_path, 5_000)
    dossier.settings = settings
    _big(dossier, "huge.md", 40_000)

    with pytest.raises(PromptTooLarge):
        _assemble(dossier, settings, _spec(inputs=["intake:data/huge.md"]))


def test_allow_truncate_marks_the_largest_optional_input(dossier: Dossier, tmp_path: Path) -> None:
    settings = _budget(tmp_path, 8_000)
    dossier.settings = settings
    _big(dossier, "huge.md", 40_000)

    result = _assemble(
        dossier,
        settings,
        _spec(optional_inputs=["intake:data/huge.md"]),
        allow_truncate=True,
    )
    assert result.truncated == ["intake:data/huge.md"]
    assert "[TRUNCATED at " in result.text
    assert result.chars <= 8_000


def test_allow_truncate_never_truncates_a_required_input(dossier: Dossier, tmp_path: Path) -> None:
    settings = _budget(tmp_path, 5_000)
    dossier.settings = settings
    _big(dossier, "huge.md", 40_000)

    with pytest.raises(PromptTooLarge):
        _assemble(
            dossier,
            settings,
            _spec(inputs=["intake:data/huge.md"]),
            allow_truncate=True,
        )


# ---------------------------------------------------------------------------
# transport hint
# ---------------------------------------------------------------------------


def test_transport_hint_switches_at_the_argv_limit(settings: Settings) -> None:
    assert transport_hint("x" * 1_000, settings) == "gateway-call"
    assert transport_hint("x" * 700_000, settings) == "agent-cli"


def test_prompt_written_to_disk_is_the_assembled_text(
    dossier: Dossier, settings: Settings, tmp_path: Path
) -> None:
    result = _assemble(dossier, settings, _spec())
    path = result.write(tmp_path / "runs" / "c1" / "prompt.md")
    assert path.read_text(encoding="utf-8") == result.text


# ---------------------------------------------------------------------------
# against the real prompt library
# ---------------------------------------------------------------------------


def test_every_review_prompt_assembles_without_phase_outputs(
    dossier: Dossier, settings: Settings, playbook
) -> None:
    """Blindness by construction: review prompts need only the package."""
    package_dir = dossier.root / "06-review" / "v1-2026-01-01" / "package"
    package_dir.mkdir(parents=True)
    for name, text in (
        ("description.md", "[0001] text"),
        ("figures.md", "## Figure Instructions\n\nFig 1"),
        ("claims.md", "1. A thing."),
        ("registry.json", '{"entries": []}'),
    ):
        (package_dir / name).write_text(text, encoding="utf-8")
    dossier.write_file(dossier.registry_path, '{"entries": []}', overwrite=True)
    dossier.write_file(dossier.crosscheck_path, '{"ok": true}', overwrite=True)
    _lock(dossier)
    state = new_state(dossier.slug, "Demo", ["US"])

    for prompt_id in ("28", "32", "37", "38", "40a"):
        spec = playbook.get(prompt_id)
        ctx = InputContext(package_dir=package_dir)
        result = assemble(dossier, state, spec, ctx, settings)
        assert "## Rationale" not in result.text
        assert result.chars > 0
