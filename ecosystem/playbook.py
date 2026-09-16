"""Loader and validator for `prompts/*.md`.

The prompt files are data, not code: the playbook text lives there verbatim with
a YAML frontmatter header naming the agent, the kind and the dossier inputs.
`validate()` is what stops a malformed playbook from being discovered halfway
through a 40-call run - it checks ids, agents, phases and every input spec
against the one grammar in `dossier.validate_spec`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ecosystem.config import AGENT_IDS, PHASES
from ecosystem.dossier import UnknownInputSpec, split_frontmatter, validate_spec

log = logging.getLogger("ipfe")

__all__ = [
    "REQUIRED_PROMPT_IDS",
    "Playbook",
    "PlaybookError",
    "PromptSpec",
    "load_all",
    "load_fragment",
    "validate",
]

PromptKind = Literal["analysis", "drafting", "review", "compose", "revise", "prose"]

# Documentation that lives beside the prompts. `_`-prefixed files are fragments;
# these are neither, so they are named explicitly rather than guessed at.
_NOT_PROMPTS: Final[frozenset[str]] = frozenset({"readme.md", "index.md", "notes.md"})

# Runner-owned templates that fill the playbook's gaps (plan s3.2).
RUNNER_PROMPT_IDS: Final[tuple[str, ...]] = (
    "x-p4-compose-description",
    "x-p4-revise",
    "x-p5-revise",
    "x-loopback-p3",
    "x-formal-fix",
    "x-gate-brief",
    "x-package-memo",
)

# Everything the pipeline may ask for. Compared case-insensitively.
REQUIRED_PROMPT_IDS: Final[frozenset[str]] = frozenset(
    [f"{n:02d}" for n in range(1, 41)] + ["20a", "39a", "40a", "gate14", *RUNNER_PROMPT_IDS]
)


class PlaybookError(RuntimeError):
    """The prompt library is unusable. Carries every problem found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        joined = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"prompt library is invalid:\n{joined}")


class PromptSpec(BaseModel):
    """One prompt file: frontmatter + the instruction text sent to the agent."""

    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    phase: str
    agent: str
    kind: PromptKind
    inputs: list[str] = Field(default_factory=list)
    optional_inputs: list[str] = Field(default_factory=list)
    deliverable_sections: list[str] = Field(default_factory=list)
    timeout_s: int = 1200
    # Set only where a downstream prompt genuinely needs upstream reasoning;
    # ip-reviewer inputs must never set it (blindness).
    include_rationale: bool = False

    body: str = ""
    path: Path | None = None

    @property
    def key(self) -> str:
        return self.id.lower()

    @property
    def all_inputs(self) -> list[str]:
        return [*self.inputs, *self.optional_inputs]


class Playbook(BaseModel):
    """The loaded prompt library plus its directory, so fragments stay reachable."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    prompts_dir: Path
    prompts: dict[str, PromptSpec] = Field(default_factory=dict)

    def get(self, prompt_id: str) -> PromptSpec:
        try:
            return self.prompts[prompt_id]
        except KeyError:
            match = next(
                (p for p in self.prompts.values() if p.key == prompt_id.lower()),
                None,
            )
            if match is None:
                raise KeyError(f"no prompt with id {prompt_id!r}") from None
            return match

    def fragment(self, name: str) -> str:
        return load_fragment(name, self.prompts_dir)

    def for_phase(self, phase: str) -> list[PromptSpec]:
        return [p for p in self.prompts.values() if p.phase == phase]


def load_all(prompts_dir: Path | str) -> dict[str, PromptSpec]:
    """Load every non-fragment `*.md`. Ids come from frontmatter, never the filename."""
    directory = Path(prompts_dir)
    if not directory.is_dir():
        raise PlaybookError([f"prompts directory does not exist: {directory}"])

    prompts: dict[str, PromptSpec] = {}
    problems: list[str] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("_") or path.name.lower() in _NOT_PROMPTS:
            continue
        try:
            spec = load_prompt(path)
        except PlaybookError as exc:
            problems.extend(exc.problems)
            continue
        if spec.id in prompts:
            problems.append(
                f"duplicate prompt id {spec.id!r} in {path.name} "
                f"and {getattr(prompts[spec.id].path, 'name', '?')}"
            )
            continue
        prompts[spec.id] = spec
    if problems:
        raise PlaybookError(problems)
    return prompts


def load_prompt(path: Path) -> PromptSpec:
    """Parse one prompt file. A missing header is fatal: the runner cannot guess a kind."""
    text = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    if not frontmatter:
        raise PlaybookError([f"{path.name}: no YAML frontmatter"])
    data = dict(frontmatter)
    data["id"] = str(data.get("id", "")).strip()
    data["body"] = body.strip()
    data["path"] = path
    try:
        return PromptSpec.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise PlaybookError([f"{path.name}: {details}"]) from exc


def load_fragment(name: str, prompts_dir: Path | str) -> str:
    """Raw text of a `_`-prefixed shared fragment (`_header`, `_contract-drafting`)."""
    directory = Path(prompts_dir)
    stem = name if name.startswith("_") else f"_{name}"
    path = directory / (stem if stem.endswith(".md") else f"{stem}.md")
    if not path.is_file():
        raise PlaybookError([f"fragment not found: {path}"])
    return path.read_text(encoding="utf-8")


def validate(
    prompts: dict[str, PromptSpec],
    require_ids: bool = True,
) -> None:
    """Fail loudly on anything that would break a run, reporting every problem.

    Input specs are checked syntactically only: whether a file exists is a
    per-matter question, answered by `Dossier.resolve_input` at run time.
    """
    problems: list[str] = []
    seen: dict[str, str] = {}

    for prompt_id, spec in sorted(prompts.items()):
        where = getattr(spec.path, "name", prompt_id)
        if spec.key in seen:
            problems.append(f"{where}: id {spec.id!r} collides with {seen[spec.key]}")
        seen[spec.key] = where

        if spec.agent not in AGENT_IDS:
            problems.append(
                f"{where}: agent {spec.agent!r} is not one of {', '.join(AGENT_IDS)}"
            )
        if spec.phase not in PHASES:
            problems.append(f"{where}: phase {spec.phase!r} is not one of {', '.join(PHASES)}")
        if spec.timeout_s <= 0:
            problems.append(f"{where}: timeout_s must be positive")
        if not spec.body.strip():
            problems.append(f"{where}: prompt body is empty")

        for input_spec in spec.all_inputs:
            try:
                validate_spec(input_spec)
            except UnknownInputSpec:
                problems.append(f"{where}: unknown input spec {input_spec!r}")
        overlap = set(spec.inputs) & set(spec.optional_inputs)
        if overlap:
            problems.append(
                f"{where}: {', '.join(sorted(overlap))} listed as both required and optional"
            )

    if require_ids:
        present = {spec.key for spec in prompts.values()}
        for required in sorted(REQUIRED_PROMPT_IDS):
            if required not in present:
                problems.append(f"required prompt id {required!r} is missing from the library")

    if problems:
        raise PlaybookError(problems)


def load_playbook(prompts_dir: Path | str, require_ids: bool = True) -> Playbook:
    """Load and validate in one step - the form the CLI and runner use."""
    directory = Path(prompts_dir)
    prompts = load_all(directory)
    validate(prompts, require_ids=require_ids)
    return Playbook(prompts_dir=directory, prompts=prompts)
