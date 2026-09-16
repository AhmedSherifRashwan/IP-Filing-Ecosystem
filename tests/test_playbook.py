from __future__ import annotations

from pathlib import Path

import pytest

from ecosystem.playbook import (
    REQUIRED_PROMPT_IDS,
    PlaybookError,
    load_all,
    load_fragment,
    load_playbook,
    validate,
)

PROMPT = """---
id: "{id}"
title: {title}
phase: {phase}
agent: {agent}
kind: {kind}
inputs: {inputs}
optional_inputs: {optional}
deliverable_sections:
  - Table
timeout_s: {timeout}
include_rationale: {rationale}
---

Do the thing, using the embedded dossier files.
"""


def write_prompt(
    directory: Path,
    prompt_id: str,
    *,
    title: str = "A prompt",
    phase: str = "P1",
    agent: str = "ip-disclosure",
    kind: str = "analysis",
    inputs: str = "['intake:disclosure']",
    optional: str = "[]",
    timeout: int = 900,
    rationale: str = "false",
    filename: str | None = None,
) -> Path:
    path = directory / (filename or f"{prompt_id}-prompt.md")
    path.write_text(
        PROMPT.format(
            id=prompt_id,
            title=title,
            phase=phase,
            agent=agent,
            kind=kind,
            inputs=inputs,
            optional=optional,
            timeout=timeout,
            rationale=rationale,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "prompts"
    directory.mkdir()
    return directory


def test_load_all_reads_frontmatter_and_body(prompts_dir: Path) -> None:
    write_prompt(prompts_dir, "01", title="Invention extraction", timeout=1500)
    write_prompt(prompts_dir, "20a", phase="P4", agent="ip-spec", kind="drafting")
    (prompts_dir / "_header.md").write_text("shared header")

    prompts = load_all(prompts_dir)

    assert set(prompts) == {"01", "20a"}
    assert prompts["01"].title == "Invention extraction"
    assert prompts["01"].timeout_s == 1500
    assert prompts["01"].inputs == ["intake:disclosure"]
    assert prompts["01"].include_rationale is False
    assert prompts["01"].body.startswith("Do the thing")
    assert prompts["20a"].agent == "ip-spec"


def test_fragments_are_not_loaded_as_prompts_and_load_by_name(prompts_dir: Path) -> None:
    (prompts_dir / "_contract-drafting.md").write_text("## Output contract")
    write_prompt(prompts_dir, "01")

    assert set(load_all(prompts_dir)) == {"01"}
    assert load_fragment("_contract-drafting", prompts_dir) == "## Output contract"
    assert load_fragment("contract-drafting.md", prompts_dir) == "## Output contract"
    with pytest.raises(PlaybookError):
        load_fragment("_missing", prompts_dir)


def test_duplicate_ids_are_rejected(prompts_dir: Path) -> None:
    write_prompt(prompts_dir, "01", filename="01-a.md")
    write_prompt(prompts_dir, "01", filename="01-b.md")

    with pytest.raises(PlaybookError) as excinfo:
        load_all(prompts_dir)
    assert "duplicate prompt id" in str(excinfo.value)


def test_missing_frontmatter_or_bad_kind_is_reported(prompts_dir: Path) -> None:
    (prompts_dir / "bare.md").write_text("no header at all")
    with pytest.raises(PlaybookError) as excinfo:
        load_all(prompts_dir)
    assert "no YAML frontmatter" in str(excinfo.value)

    (prompts_dir / "bare.md").unlink()
    write_prompt(prompts_dir, "02", kind="interpretive-dance")
    with pytest.raises(PlaybookError) as excinfo:
        load_all(prompts_dir)
    assert "kind" in str(excinfo.value)


def test_validate_catches_bad_agents_phases_and_input_specs(prompts_dir: Path) -> None:
    write_prompt(prompts_dir, "01", agent="ip-nonexistent")
    write_prompt(prompts_dir, "02", phase="P9")
    write_prompt(prompts_dir, "03", inputs="['out:P9/04', 'intake:disclosure']")
    write_prompt(prompts_dir, "04", inputs="['terminology']", optional="['terminology']")

    with pytest.raises(PlaybookError) as excinfo:
        validate(load_all(prompts_dir), require_ids=False)

    problems = "\n".join(excinfo.value.problems)
    assert "ip-nonexistent" in problems
    assert "P9" in problems
    assert "unknown input spec 'out:P9/04'" in problems
    assert "both required and optional" in problems


def test_validate_requires_the_whole_playbook(prompts_dir: Path) -> None:
    write_prompt(prompts_dir, "01")

    with pytest.raises(PlaybookError) as excinfo:
        validate(load_all(prompts_dir), require_ids=True)

    problems = "\n".join(excinfo.value.problems)
    assert "required prompt id '02' is missing" in problems
    assert "required prompt id 'x-p4-compose-description' is missing" in problems
    assert "required prompt id 'gate14' is missing" in problems


def test_a_complete_library_validates_and_loads(prompts_dir: Path) -> None:
    for prompt_id in sorted(REQUIRED_PROMPT_IDS):
        write_prompt(prompts_dir, prompt_id, filename=f"{prompt_id}.md")

    playbook = load_playbook(prompts_dir)

    assert len(playbook.prompts) == len(REQUIRED_PROMPT_IDS)
    assert playbook.get("20a").id == "20a"
    assert playbook.get("X-P4-REVISE").id == "x-p4-revise"
    assert [p.id for p in playbook.for_phase("P1")]


def test_required_ids_cover_the_playbook_and_the_runner_templates() -> None:
    assert "01" in REQUIRED_PROMPT_IDS and "40" in REQUIRED_PROMPT_IDS
    assert {"20a", "39a", "40a", "gate14"} <= REQUIRED_PROMPT_IDS
    assert "x-loopback-p3" in REQUIRED_PROMPT_IDS
    assert len(REQUIRED_PROMPT_IDS) == 40 + 3 + 1 + 7


def test_missing_prompts_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PlaybookError):
        load_all(tmp_path / "nope")


def test_documentation_files_are_not_mistaken_for_prompts(prompts_dir: Path) -> None:
    write_prompt(prompts_dir, "01")
    (prompts_dir / "README.md").write_text("How the prompt library is organised.")

    assert set(load_all(prompts_dir)) == {"01"}


REPO_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


@pytest.mark.skipif(
    not (REPO_PROMPTS / "01-invention-extraction.md").is_file(),
    reason="the real prompt library is not in place yet",
)
def test_the_repo_prompt_library_loads_and_validates() -> None:
    """Guards the seam: a prompt file may not name an input the runner cannot resolve."""
    playbook = load_playbook(REPO_PROMPTS)

    assert len(playbook.prompts) >= len(REQUIRED_PROMPT_IDS)
    assert playbook.fragment("_header").strip()
