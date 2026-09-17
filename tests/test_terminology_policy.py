"""The lock blocks only where text becomes specification or claims."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ecosystem.calls import Caller, TerminologyBlocked
from ecosystem.terminology import TermReport, Violation


def _caller(tmp_path: Path, policy: str = "block") -> Caller:
    caller = Caller.__new__(Caller)
    caller.settings = SimpleNamespace(terminology_policy=policy)
    caller.state = SimpleNamespace(
        terminology=SimpleNamespace(drift_logged=0, violations_blocked=0)
    )
    caller.dossier = SimpleNamespace(
        drift_path=tmp_path / "drift.jsonl", mark_status=lambda *_a, **_k: None
    )
    caller.events = SimpleNamespace(emit=lambda *_a, **_k: None)
    return caller


def _report() -> TermReport:
    return TermReport(
        violations=[Violation(term="slotted", rule="avoid-hit", row="seat element", context="")],
        drift=[],
        warnings=[],
    )


@pytest.mark.parametrize("phase", ["P1", "P2", "P3"])
def test_early_phases_warn_instead_of_blocking(tmp_path: Path, phase: str) -> None:
    caller = _caller(tmp_path)
    result = SimpleNamespace(call_id="c", output_path=None, terminology_warned=[])
    spec = SimpleNamespace(phase=phase, id="11")
    caller._settle_terminology(result, spec, _report())  # no raise


@pytest.mark.parametrize("phase", ["P4", "P5", "REVIEW"])
def test_specification_and_claims_phases_block(tmp_path: Path, phase: str) -> None:
    caller = _caller(tmp_path)
    result = SimpleNamespace(call_id="c", output_path=None, terminology_warned=[])
    spec = SimpleNamespace(phase=phase, id="22")
    with pytest.raises(TerminologyBlocked):
        caller._settle_terminology(result, spec, _report())


def test_unattended_runs_never_block_on_terminology(tmp_path: Path) -> None:
    caller = _caller(tmp_path)
    caller.state.unattended = True
    result = SimpleNamespace(call_id="c", output_path=None, terminology_warned=[])
    spec = SimpleNamespace(phase="P4", id="19")
    caller._settle_terminology(result, spec, _report())  # logged, not raised
