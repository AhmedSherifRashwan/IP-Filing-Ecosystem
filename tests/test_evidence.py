"""The evidence pack: what it covers, what it proves, and how it fails.

The pack is only worth something if verification actually notices a change, so
the centre of this file is the tamper test: edit one byte of one intake file and
`verify_evidence` must name that file and produce a different root hash.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecosystem import evidence, package
from ecosystem.config import Settings
from ecosystem.dossier import Dossier
from ecosystem.ledger import read_ledger
from ecosystem.state import AgentRun, MatterState, new_state
from ecosystem.state import save as save_state
from tests.conftest import DRAFTING_BODY, write_version
from tests.replay_builder import CLAIMS, DESCRIPTION, REGISTRY_TABLE, review_reply

FIGURES_20A = f"""## Deliverable

### Figure Instructions

Figure 2: section through the core body 20 showing the seat ring 30.

### Reference-Numeral Registry

{REGISTRY_TABLE}

## Rationale

Three figures are the minimum that supports the claims.

## Assumptions

- A1: none.

## Open Questions

- Q1: none.
"""

FIGURES_20 = """## Deliverable

### Figure Plan

Figure 1 is an exterior view; Figure 2 a section; Figure 3 the purge gap 60 open.

## Rationale

Secret drafting reasoning.

## Assumptions

- A1: none.

## Open Questions

- Q1: none.
"""


@pytest.fixture
def state(dossier: Dossier) -> MatterState:
    matter = new_state(dossier.slug, "Demo matter", ["US", "EP"])
    matter.gates["1"].status = "decided"
    matter.gates["1"].decision = "accept"
    matter.gates["1"].decided_by = "ahmed"
    matter.gates["1"].decided_at = "2026-09-17T09:00:00+00:00"
    matter.terminology.terms = 4
    matter.counters["contract_corrections"] = 1
    return matter


def _call(dossier: Dossier, state: MatterState, call_id: str, phase: str, prompt: str) -> None:
    """A finished agent call, on disk and in state, as the runner would leave it."""
    run_dir = dossier.run_dir(call_id)
    (run_dir / "prompt.md").write_text(f"prompt for {call_id}\n", encoding="utf-8")
    (run_dir / "reply.md").write_text(f"reply for {call_id}\n", encoding="utf-8")
    state.agent_runs.append(
        AgentRun(
            call_id=call_id,
            phase=phase,
            prompt=prompt,
            agent_id=f"ip-{phase.lower()}",
            model="fake/model",
            provider="fake",
            transport="fake",
            session_key=f"agent:ip-{phase.lower()}:{call_id}",
            idempotency_key=f"demo:{phase}:{prompt}:v1:a0",
            status="ok",
            duration_ms=1234,
            prompt_chars=20,
            reply_chars=19,
            finished_at="2026-09-17T09:05:00+00:00",
        )
    )


def _packaged(dossier: Dossier, state: MatterState) -> Path:
    """Drive a matter to a final package the short way: no transport, no runner."""
    (dossier.intake_dir / "disclosure.md").write_text(
        "# Disclosure\n\nA valve core with a seat ring.\n", encoding="utf-8"
    )
    write_version(dossier, "P1", {"01": DRAFTING_BODY}, envelopes={"01": {"prompt": "01"}})
    _call(dossier, state, "demo-p1-01-v1-a0", "P1", "01")

    p4 = write_version(
        dossier,
        "P4",
        {"20": FIGURES_20, "20a": FIGURES_20A},
        extra_files={"description.md": DESCRIPTION},
    )
    state.phase("P4").current_version = p4
    _call(dossier, state, "demo-p4-20-v1-a0", "P4", "20")
    p5 = write_version(dossier, "P5", {"25": FIGURES_20}, extra_files={"claims.md": CLAIMS})
    state.phase("P5").current_version = p5
    dossier.write_file(dossier.registry_path, '{"entries": [], "source": "20a"}')
    dossier.write_file(dossier.drift_path, '{"term": "purge clearance", "reason": "synonym"}\n')
    dossier.write_file(dossier.crosscheck_path, '{"ok": true}\n')

    review = dossier.new_version("REVIEW", "initial")
    state.phase("REVIEW").current_version = review.v
    package.build_review_package(dossier, state, review.v)
    for prompt_id in ("28", "31", "39"):
        dossier.write_output(
            "REVIEW",
            review.v,
            prompt_id,
            "review",
            review_reply(prompt_id, ["Matrix"]).split("```json")[0],
            {"agent": "ip-reviewer"},
        )
    _call(dossier, state, "demo-review-28-v1-a0", "REVIEW", "28")
    (review.path / "findings.json").write_text('{"findings": []}', encoding="utf-8")
    dossier.complete_version("REVIEW", review.v)

    version = dossier.new_version("PACKAGE", "initial")
    state.phase("PACKAGE").current_version = version.v
    save_state(state, dossier.state_path)
    return package.build_final_package(dossier, state, version.v)


@pytest.fixture
def packaged(dossier: Dossier, state: MatterState) -> Path:
    return _packaged(dossier, state)


def _pack(dossier: Dossier) -> Path:
    directory = evidence.latest_evidence_dir(dossier)
    assert directory is not None, "the final package should have built an evidence pack"
    return directory


def _manifest(directory: Path) -> dict:
    return json.loads((directory / evidence.MANIFEST_NAME).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# what the pack contains
# ---------------------------------------------------------------------------


def test_building_the_final_package_freezes_a_pack(dossier: Dossier, packaged: Path) -> None:
    directory = _pack(dossier)
    assert directory.parent.name == "evidence"
    assert directory.name.startswith("v1-")
    names = {path.name for path in directory.iterdir()}
    assert names == {
        "chain-of-custody.json",
        "provenance.jsonl",
        "gate-decisions.json",
        "terminology-drift.jsonl",
        "numeral-cross-check.json",
        "events.jsonl",
        "support-check.json",
        "README.md",
        "EVIDENCE.md",
    }


def test_the_manifest_covers_intake_outputs_runs_and_both_packages(
    dossier: Dossier, packaged: Path
) -> None:
    paths = {record["path"] for record in _manifest(_pack(dossier))["records"]}
    assert "00-intake/disclosure.md" in paths
    assert any(path.startswith("01-disclosure/v1-") for path in paths)
    assert "runs/demo-p1-01-v1-a0/prompt.md" in paths
    assert "runs/demo-p1-01-v1-a0/reply.md" in paths
    assert any(path.endswith("/package/claims.md") for path in paths)
    assert f"{packaged.relative_to(dossier.root)}/description.md" in paths
    assert f"{packaged.relative_to(dossier.root)}/MANIFEST.json" in paths
    # The pack never vouches for itself.
    assert not any(path.startswith("evidence/") for path in paths)


def test_every_record_carries_a_hash_a_size_and_a_time(dossier: Dossier, packaged: Path) -> None:
    for record in _manifest(_pack(dossier))["records"]:
        assert len(record["sha256"]) == 64
        assert record["bytes"] > 0
        assert record["mtime_utc"].endswith("+00:00")


def test_the_manifest_says_which_code_built_it(dossier: Dossier, packaged: Path) -> None:
    manifest = _manifest(_pack(dossier))
    assert manifest["matter"] == "demo"
    assert manifest["package_version"] == 1
    assert manifest["tool_version"]
    assert manifest["openclaw_version"] is None
    assert len(manifest["dossier_root_hash"]) == 64


def test_provenance_has_one_line_per_call_with_both_hashes(
    dossier: Dossier, state: MatterState, packaged: Path
) -> None:
    lines = (_pack(dossier) / "provenance.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(state.agent_runs)
    entry = json.loads(lines[0])
    assert entry["call_id"] == "demo-p1-01-v1-a0"
    assert entry["agent_id"] == "ip-p1"
    assert entry["model"] == "fake/model"
    assert entry["transport"] == "fake"
    assert len(entry["prompt_sha256"]) == 64
    assert len(entry["reply_sha256"]) == 64
    assert entry["duration_ms"] == 1234
    assert entry["status"] == "ok"


def test_the_deterministic_checks_are_copied_in(dossier: Dossier, packaged: Path) -> None:
    directory = _pack(dossier)
    assert "purge clearance" in (directory / "terminology-drift.jsonl").read_text(encoding="utf-8")
    assert json.loads((directory / "numeral-cross-check.json").read_text(encoding="utf-8"))["ok"]
    support = json.loads((directory / "support-check.json").read_text(encoding="utf-8"))
    assert support["claims"]
    gates = json.loads((directory / "gate-decisions.json").read_text(encoding="utf-8"))
    assert gates["1"]["decided_by"] == "ahmed"


def test_the_summary_is_honest_about_what_it_does_not_prove(
    dossier: Dossier, packaged: Path
) -> None:
    summary = (_pack(dossier) / "EVIDENCE.md").read_text(encoding="utf-8")
    assert "does not establish inventorship, priority or patentability" in summary
    assert "ahmed" in summary  # who decided Gate 1
    assert "fake/model" in summary
    assert _manifest(_pack(dossier))["dossier_root_hash"] in summary
    readme = (_pack(dossier) / "README.md").read_text(encoding="utf-8")
    assert "ipfe evidence --matter demo --verify" in readme


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------


def test_a_fresh_pack_verifies(dossier: Dossier, packaged: Path) -> None:
    report = evidence.verify_evidence(dossier, _pack(dossier))
    assert report.ok is True
    assert report.missing == []
    assert report.mismatched == []
    assert report.root_hash_ok is True
    assert report.actual_root_hash == report.recorded_root_hash
    assert report.checked == len(_manifest(_pack(dossier))["records"])


def test_tampering_with_one_file_is_named_and_moves_the_root_hash(
    dossier: Dossier, packaged: Path
) -> None:
    disclosure = dossier.intake_dir / "disclosure.md"
    disclosure.write_text(
        disclosure.read_text(encoding="utf-8") + "\nA sentence nobody drafted.\n", encoding="utf-8"
    )

    report = evidence.verify_evidence(dossier, _pack(dossier))

    assert report.ok is False
    assert [item.path for item in report.mismatched] == ["00-intake/disclosure.md"]
    assert report.mismatched[0].recorded != report.mismatched[0].actual
    # The manifest is still internally consistent; the dossier is not.
    assert report.root_hash_ok is True
    assert report.actual_root_hash != report.recorded_root_hash


def test_a_deleted_file_is_reported_as_missing(dossier: Dossier, packaged: Path) -> None:
    (dossier.intake_dir / "disclosure.md").unlink()

    report = evidence.verify_evidence(dossier, _pack(dossier))

    assert report.ok is False
    assert report.missing == ["00-intake/disclosure.md"]
    assert report.mismatched == []


def test_an_edited_manifest_fails_its_own_root_hash(dossier: Dossier, packaged: Path) -> None:
    """Somebody who rewrites a record must also forge the root - and cannot quietly."""
    directory = _pack(dossier)
    manifest = _manifest(directory)
    manifest["records"][0]["sha256"] = "0" * 64
    (directory / evidence.MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), "utf-8")

    report = evidence.verify_evidence(dossier, directory)

    assert report.root_hash_ok is False
    assert report.ok is False


def test_verifying_a_pack_that_is_not_there_says_so(dossier: Dossier) -> None:
    with pytest.raises(FileNotFoundError):
        evidence.verify_evidence(dossier, dossier.root / "evidence" / "v9-2026-01-01")


def test_the_root_hash_is_reproducible_by_hand(dossier: Dossier, packaged: Path) -> None:
    import hashlib

    manifest = _manifest(_pack(dossier))
    lines = "".join(
        f"{record['path']}\n{record['sha256']}\n"
        for record in sorted(manifest["records"], key=lambda item: item["path"])
    )
    assert hashlib.sha256(lines.encode("utf-8")).hexdigest() == manifest["dossier_root_hash"]


# ---------------------------------------------------------------------------
# the package hook
# ---------------------------------------------------------------------------


def test_the_package_manifest_points_at_the_pack_and_the_support_check(
    dossier: Dossier, packaged: Path
) -> None:
    manifest = json.loads((packaged / "MANIFEST.json").read_text(encoding="utf-8"))
    members = {member["name"]: member for member in manifest["members"]}
    assert members["evidence"]["path"] == str(_pack(dossier).relative_to(dossier.root))
    assert "support-check.md" in members
    assert (packaged / "support-check.md").is_file()
    assert "Prompt 28" in (packaged / "support-check.md").read_text(encoding="utf-8")


def test_the_root_hash_is_anchored_in_the_repo_ledger(
    dossier: Dossier, settings: Settings, packaged: Path
) -> None:
    entries = read_ledger(settings.repo_root, "demo")
    assert len(entries) == 1
    assert entries[0]["root_hash"] == _manifest(_pack(dossier))["dossier_root_hash"]
    assert entries[0]["package_version"] == 1
    assert entries[0]["ots"] is False


def test_a_dossier_outside_the_repo_is_not_anchored(tmp_path: Path, state: MatterState) -> None:
    """A relocated `IPFE_DOSSIERS_DIR` must not write into somebody's checkout."""
    settings = Settings(repo_root=tmp_path / "repo", dossiers_dir=tmp_path / "elsewhere")
    root = settings.dossier_root("demo")
    dossier = Dossier(root, settings).create("demo", "Demo matter", ["US"])

    _packaged(dossier, state)

    assert evidence.latest_evidence_dir(dossier) is not None
    assert not (tmp_path / "repo" / "evidence-ledger").exists()


def test_a_failing_evidence_build_never_blocks_the_package(
    dossier: Dossier, state: MatterState, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> Path:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(evidence, "build_evidence", explode)
    target = _packaged(dossier, state)

    assert (target / "MANIFEST.json").is_file()
    assert evidence.latest_evidence_dir(dossier) is None


def test_the_pack_is_refreshed_when_the_cover_memo_lands(
    dossier: Dossier, state: MatterState, packaged: Path
) -> None:
    """The memo is written after the package is built; verification must still pass."""
    before = _manifest(_pack(dossier))["dossier_root_hash"]

    package.write_cover_memo(dossier, packaged, "# Cover memo\n\nWritten by the orchestrator.")

    after = _manifest(_pack(dossier))["dossier_root_hash"]
    assert after != before
    assert evidence.verify_evidence(dossier, _pack(dossier)).ok is True


def test_rebuilding_reuses_the_same_pack_directory(
    dossier: Dossier, state: MatterState, packaged: Path
) -> None:
    first = _pack(dossier)
    evidence.build_evidence(dossier, state, packaged)
    assert _pack(dossier) == first
    assert len(list((dossier.root / "evidence").iterdir())) == 1
