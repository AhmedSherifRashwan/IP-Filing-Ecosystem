"""Shared fixtures. Every test runs against a temp repo root: no network, no dossiers/."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ecosystem.config import Settings
from ecosystem.dossier import Dossier

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """A run rooted in a temp dir, reading the repo's real prompt library.

    Dossiers, runs and state go to `tmp_path`; `prompts/` is the checked-in
    library, because the assembly and runner tests are only meaningful against
    the prompts that will actually be sent.
    """
    return Settings(
        repo_root=tmp_path,
        prompts_dir=REPO_ROOT / "prompts",
        openclaw_bin="/nonexistent/openclaw",
    )


@pytest.fixture
def dossier(settings: Settings) -> Dossier:
    root = settings.dossier_root("demo")
    return Dossier(root, settings).create("demo", "Demo matter", ["US", "EP"])


def write_version(
    dossier: Dossier,
    phase: str,
    outputs: dict[str, str],
    trigger: str = "initial",
    complete: bool = True,
    envelopes: dict[str, dict[str, Any]] | None = None,
    extra_files: dict[str, str] | None = None,
) -> int:
    """Create one complete phase version with the given `<id>: body` outputs."""
    version = dossier.new_version(phase, trigger)
    for prompt_id, body in outputs.items():
        dossier.write_output(
            phase,
            version.v,
            prompt_id,
            "output",
            body,
            {"agent": "test-agent"},
            envelope=(envelopes or {}).get(prompt_id),
        )
    for name, text in (extra_files or {}).items():
        (version.path / name).write_text(text, encoding="utf-8")
    if complete:
        dossier.complete_version(phase, version.v)
    return version.v


DRAFTING_BODY = """## Deliverable

The widget comprises a housing 10 and a controller 120.

## Rationale

Chosen because the alternative narrows scope.

## Assumptions

- A1: steel is available.

## Open Questions

- Q1: what temperature range?
"""
