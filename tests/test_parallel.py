"""Independent prompts run together; dependent ones wait."""

from __future__ import annotations

from ecosystem.playbook import load_playbook
from ecosystem.runner import Runner
from tests.conftest import REPO_ROOT


def _levels(phase: str, prompts: list[str], **kwargs) -> list[list[str]]:
    playbook = load_playbook(REPO_ROOT / "prompts")
    bound = Runner._levels.__get__(type("R", (), {"playbook": playbook})(), Runner)
    return bound(phase, prompts, **kwargs)


def test_review_prompts_are_one_parallel_level() -> None:
    prompts = ["28", "29", "30", "31", "32", "33", "34", "35", "36", "37", "38", "40a"]
    assert _levels("REVIEW", prompts) == [prompts]


def test_phase_1_is_a_strict_chain() -> None:
    assert _levels("P1", ["01", "02", "03", "04", "05"]) == [["01"], ["02"], ["03"], ["04"], ["05"]]


def test_phase_3_pairs_independent_prompts() -> None:
    assert _levels("P3", ["11", "12", "13", "14", "15"]) == [["11", "13"], ["12"], ["14", "15"]]


def test_both_reviewers_land_in_the_same_level() -> None:
    """The second reviewer is a wider batch, not a second pass."""
    prompts = ["28", "32", "37"]
    doubled = [*prompts, *(f"{p}b" for p in prompts)]
    levels = _levels(
        "REVIEW",
        doubled,
        source={f"{p}b": p for p in prompts},
        lanes={f"{p}b": "b" for p in prompts},
    )
    assert levels == [doubled]


def test_a_reviewers_chain_never_reaches_across_lanes() -> None:
    """Phase 1 is a strict chain, so it shows what a doubled chain would do:
    each lane waits on itself and neither waits on the other."""
    prompts = ["01", "02", "03"]
    doubled = [*prompts, *(f"{p}b" for p in prompts)]
    levels = _levels(
        "P1",
        doubled,
        source={f"{p}b": p for p in prompts},
        lanes={f"{p}b": "b" for p in prompts},
    )
    assert levels == [["01", "01b"], ["02", "02b"], ["03", "03b"]]
