"""`ipfe evidence`, `ipfe verify-support`, `ipfe questions` through the real CLI.

Exit codes are the contract: 6 when the inputs a command needs do not exist, 4
when `--verify` finds that the dossier has moved under a pack, 0 for the two
advisory commands whatever they report. The matter is driven to a finished
package by the replay transport, so these run against the same artefacts a live
run produces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecosystem.cli import EXIT, main
from ecosystem.playbook import load_playbook
from tests.conftest import REPO_ROOT
from tests.replay_builder import (
    Scenario,
    claims_correction,
    fixture_key,
    gate14_answers,
    materialise,
    novelty_finding,
)

EXAMPLE = REPO_ROOT / "tests" / "fixtures" / "example-matter"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A whole repo root in a temp dir, so the evidence ledger lands there too."""
    monkeypatch.setenv("IPFE_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("IPFE_PROMPTS_DIR", str(REPO_ROOT / "prompts"))
    monkeypatch.delenv("IPFE_DOSSIERS_DIR", raising=False)
    monkeypatch.delenv("IPFE_TRANSPORT", raising=False)
    monkeypatch.chdir(REPO_ROOT)
    return tmp_path / "dossiers"


@pytest.fixture
def replays(tmp_path: Path) -> Path:
    playbook = load_playbook(REPO_ROOT / "prompts")
    directory = tmp_path / "replays"
    scenario = (
        Scenario(slug="demo")
        .at(fixture_key("demo", "REVIEW", "32", 1), findings=[novelty_finding()])
        .at(fixture_key("demo", "REVIEW", "37", 1), corrections=[claims_correction()])
        .at(fixture_key("demo", "REVIEW", "gate14", 2), gate14=gate14_answers())
    )
    materialise(playbook, directory, scenario)
    return directory


def _new_matter(slug: str = "demo") -> int:
    return main(
        [
            "new-matter",
            "--slug",
            slug,
            "--title",
            "Self-purging bicycle tube valve",
            "--jurisdictions",
            "US,EP",
            "--from-example",
            str(EXAMPLE),
        ]
    )


@pytest.fixture
def packaged(env: Path, replays: Path) -> Path:
    """A matter driven unattended to Gate 3: the final package exists."""
    assert _new_matter() == EXIT["ok"]
    assert (
        main(
            [
                "run",
                "--matter",
                "demo",
                "--unattended",
                "--transport",
                "fake",
                "--fixtures",
                str(replays),
            ]
        )
        == EXIT["gate"]
    )
    return env / "demo"


# ---------------------------------------------------------------------------
# ipfe evidence
# ---------------------------------------------------------------------------


def test_the_run_leaves_a_pack_and_evidence_rebuilds_it(
    packaged: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    packs = sorted((packaged / "evidence").iterdir())
    assert len(packs) == 1

    capsys.readouterr()
    assert main(["evidence", "--matter", "demo", "--json"]) == EXIT["ok"]
    report = json.loads(capsys.readouterr().out)

    assert report["matter"] == "demo"
    assert report["package_version"] == 1
    assert len(report["root_hash"]) == 64
    assert report["files"] > 20
    assert Path(report["evidence_dir"]) == packs[0]
    assert report["ledger"].endswith("evidence-ledger/demo.jsonl")

    line = json.loads(Path(report["ledger"]).read_text(encoding="utf-8").splitlines()[-1])
    assert line["root_hash"] == report["root_hash"]


def test_evidence_prints_the_root_hash_and_how_to_verify(
    packaged: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert main(["evidence", "--matter", "demo"]) == EXIT["ok"]
    out = capsys.readouterr().out
    assert "evidence pack " in out
    assert "root hash      " in out
    assert "uv run ipfe evidence --matter demo --verify" in out


def test_no_ledger_builds_the_pack_without_anchoring(
    env: Path, packaged: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (env.parent / "evidence-ledger" / "demo.jsonl").unlink()
    capsys.readouterr()

    assert main(["evidence", "--matter", "demo", "--no-ledger", "--json"]) == EXIT["ok"]
    report = json.loads(capsys.readouterr().out)

    assert report["ledger"] is None
    assert not (env.parent / "evidence-ledger" / "demo.jsonl").exists()


def test_verify_passes_on_the_pack_the_run_wrote(
    packaged: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert main(["evidence", "--matter", "demo", "--verify"]) == EXIT["ok"]
    out = capsys.readouterr().out
    assert "verified" in out
    assert "MISSING" not in out


def test_verify_exits_4_and_names_the_file_after_a_tamper(
    packaged: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    disclosure = packaged / "00-intake" / "disclosure.md"
    disclosure.write_text(disclosure.read_text(encoding="utf-8") + "\nedited later\n", "utf-8")
    capsys.readouterr()

    assert main(["evidence", "--matter", "demo", "--verify"]) == EXIT["contract"]

    out = capsys.readouterr().out
    assert "CHANGED   00-intake/disclosure.md" in out
    assert "recomputed root hash" in out


def test_verify_json_carries_the_two_root_hashes(
    packaged: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (packaged / "00-intake" / "disclosure.md").unlink()
    capsys.readouterr()

    assert main(["evidence", "--matter", "demo", "--verify", "--json"]) == EXIT["contract"]
    report = json.loads(capsys.readouterr().out)

    assert report["ok"] is False
    assert report["missing"] == ["00-intake/disclosure.md"]
    assert report["recorded_root_hash"] != report["actual_root_hash"]


def test_evidence_without_a_package_exits_6(env: Path) -> None:
    _new_matter()
    assert main(["evidence", "--matter", "demo"]) == EXIT["precondition"]


def test_evidence_verify_without_a_pack_exits_6(
    env: Path, packaged: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    shutil.rmtree(packaged / "evidence")
    capsys.readouterr()

    assert main(["evidence", "--matter", "demo", "--verify"]) == EXIT["precondition"]
    assert "build one with" in capsys.readouterr().err


def test_evidence_for_an_unknown_matter_exits_6(env: Path) -> None:
    assert main(["evidence", "--matter", "ghost"]) == EXIT["precondition"]


# ---------------------------------------------------------------------------
# ipfe verify-support
# ---------------------------------------------------------------------------


def test_verify_support_prints_the_table_and_exits_0(
    packaged: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert main(["verify-support", "--matter", "demo"]) == EXIT["ok"]
    out = capsys.readouterr().out
    assert "| claim | limitation | anchor | status |" in out
    assert "Advisory only" in out
    assert "Prompt 28" in out


def test_verify_support_json_reports_every_limitation(
    packaged: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert main(["verify-support", "--matter", "demo", "--json"]) == EXIT["ok"]
    report = json.loads(capsys.readouterr().out)
    assert report["limitations"]
    assert report["claims"]
    assert "unsupported_count" in report


def test_verify_support_before_the_filing_text_exists_exits_6(env: Path) -> None:
    _new_matter()
    assert main(["verify-support", "--matter", "demo"]) == EXIT["precondition"]


# ---------------------------------------------------------------------------
# ipfe questions
# ---------------------------------------------------------------------------


def test_questions_ranks_what_phase_one_asked(
    env: Path, replays: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _new_matter()
    assert (
        main(["run", "--matter", "demo", "--transport", "fake", "--fixtures", str(replays)])
        == EXIT["gate"]
    )
    capsys.readouterr()

    assert main(["questions", "--matter", "demo"]) == EXIT["ok"]
    out = capsys.readouterr().out
    assert " 1. Q1" in out
    assert "Is the alloy specification fixed?" in out
    assert "why: blocks P4" in out


def test_questions_json_and_template(
    env: Path, replays: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _new_matter()
    main(["run", "--matter", "demo", "--transport", "fake", "--fixtures", str(replays)])
    capsys.readouterr()

    assert main(["questions", "--matter", "demo", "--top", "1", "--json", "--template"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert len(report["questions"]) == 1
    question = report["questions"][0]
    assert question["rank"] == 1
    assert question["blocks"] == ["P4"]
    assert question["why"]
    template = Path(report["template"])
    assert template.name == "gate-1-answers.template.md"
    assert "**Answer:**" in template.read_text(encoding="utf-8")


def test_questions_before_phase_one_exits_6(env: Path) -> None:
    _new_matter()
    assert main(["questions", "--matter", "demo"]) == EXIT["precondition"]
