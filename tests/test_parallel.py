"""Independent prompts run together; dependent ones wait."""

from __future__ import annotations

from ecosystem.playbook import load_playbook
from ecosystem.runner import Runner
from tests.conftest import REPO_ROOT


def _levels(phase: str, prompts: list[str]) -> list[list[str]]:
    playbook = load_playbook(REPO_ROOT / "prompts")
    return Runner._levels.__get__(type("R", (), {"playbook": playbook})(), Runner)(phase, prompts)


def test_review_prompts_are_one_parallel_level() -> None:
    prompts = ["28", "29", "30", "31", "32", "33", "34", "35", "36", "37", "38", "40a"]
    assert _levels("REVIEW", prompts) == [prompts]


def test_phase_1_is_a_strict_chain() -> None:
    assert _levels("P1", ["01", "02", "03", "04", "05"]) == [["01"], ["02"], ["03"], ["04"], ["05"]]


def test_phase_3_pairs_independent_prompts() -> None:
    assert _levels("P3", ["11", "12", "13", "14", "15"]) == [["11", "13"], ["12"], ["14", "15"]]
