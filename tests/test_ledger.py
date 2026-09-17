"""Anchoring a root hash outside the dossier: the ledger line, git, OpenTimestamps.

Everything here is best effort by design, so the tests are mostly about what
happens when a piece is *missing*: no git, no `ots`, no repository. In every
case one line must still be appended and nothing may raise.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ecosystem.ledger import append_ledger, ledger_path, read_ledger

ROOT_HASH = "a" * 64
OTHER_HASH = "b" * 64
BUILT_AT = "2026-09-17T09:00:00+00:00"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return completed.stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real, self-contained git repository with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "--quiet", "-m", "seed")
    return repo


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    directory = tmp_path / "dossier" / "evidence" / "v1-2026-09-17"
    directory.mkdir(parents=True)
    path = directory / "chain-of-custody.json"
    path.write_text(json.dumps({"dossier_root_hash": ROOT_HASH}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# the line itself
# ---------------------------------------------------------------------------


def test_one_line_is_appended_with_hashes_only(tmp_path: Path, manifest: Path) -> None:
    path = append_ledger(tmp_path, "demo", 1, ROOT_HASH, BUILT_AT, manifest_path=manifest)

    assert path == ledger_path(tmp_path, "demo")
    entries = read_ledger(tmp_path, "demo")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["matter"] == "demo"
    assert entry["package_version"] == 1
    assert entry["root_hash"] == ROOT_HASH
    assert entry["built_at"] == BUILT_AT
    assert entry["evidence_dir"] == "v1-2026-09-17"
    assert len(entry["manifest_sha256"]) == 64
    # Hashes and metadata only: nothing from the dossier's content.
    assert set(entry) <= {
        "at",
        "matter",
        "package_version",
        "root_hash",
        "built_at",
        "evidence_dir",
        "manifest_sha256",
        "ots",
        "ots_hint",
    }


def test_re_anchoring_the_same_root_hash_writes_nothing(tmp_path: Path) -> None:
    append_ledger(tmp_path, "demo", 1, ROOT_HASH, BUILT_AT)
    append_ledger(tmp_path, "demo", 1, ROOT_HASH, BUILT_AT)
    assert len(read_ledger(tmp_path, "demo")) == 1


def test_a_new_root_hash_appends_a_second_line(tmp_path: Path) -> None:
    append_ledger(tmp_path, "demo", 1, ROOT_HASH, BUILT_AT)
    append_ledger(tmp_path, "demo", 1, OTHER_HASH, BUILT_AT)
    entries = read_ledger(tmp_path, "demo")
    assert [entry["root_hash"] for entry in entries] == [ROOT_HASH, OTHER_HASH]


def test_a_corrupt_line_does_not_stop_the_next_one(tmp_path: Path) -> None:
    path = ledger_path(tmp_path, "demo")
    path.parent.mkdir(parents=True)
    path.write_text("not json\n", encoding="utf-8")

    append_ledger(tmp_path, "demo", 2, ROOT_HASH, BUILT_AT)
    entries = read_ledger(tmp_path, "demo")
    assert [entry["root_hash"] for entry in entries] == [ROOT_HASH]


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def test_the_ledger_file_is_committed_when_the_repo_is_there(git_repo: Path) -> None:
    append_ledger(git_repo, "demo", 3, ROOT_HASH, BUILT_AT)

    log = _git(git_repo, "log", "--oneline", "-1")
    assert f"evidence: demo v3 {ROOT_HASH[:12]}" in log
    files = _git(git_repo, "show", "--name-only", "--format=", "HEAD").split()
    assert files == ["evidence-ledger/demo.jsonl"]
    # Nothing else was swept into the commit, and the tree is clean afterwards.
    assert _git(git_repo, "status", "--porcelain") == ""


def test_other_uncommitted_work_is_left_alone(git_repo: Path) -> None:
    (git_repo / "work-in-progress.py").write_text("x = 1\n", encoding="utf-8")
    (git_repo / "README.md").write_text("edited\n", encoding="utf-8")

    append_ledger(git_repo, "demo", 1, ROOT_HASH, BUILT_AT)

    status = _git(git_repo, "status", "--porcelain")
    assert " M README.md" in status
    assert "?? work-in-progress.py" in status
    files = _git(git_repo, "show", "--name-only", "--format=", "HEAD").split()
    assert files == ["evidence-ledger/demo.jsonl"]


def test_a_staged_change_is_not_swept_into_the_evidence_commit(git_repo: Path) -> None:
    """Somebody else's index is not ours to commit, even accidentally."""
    (git_repo / "staged.py").write_text("y = 2\n", encoding="utf-8")
    _git(git_repo, "add", "staged.py")

    append_ledger(git_repo, "demo", 1, ROOT_HASH, BUILT_AT)

    files = _git(git_repo, "show", "--name-only", "--format=", "HEAD").split()
    assert files == ["evidence-ledger/demo.jsonl"]
    assert "A  staged.py" in _git(git_repo, "status", "--porcelain")


def test_a_directory_that_is_not_a_repo_still_gets_its_line(tmp_path: Path) -> None:
    append_ledger(tmp_path, "demo", 1, ROOT_HASH, BUILT_AT)
    assert len(read_ledger(tmp_path, "demo")) == 1


def test_the_commit_can_be_switched_off(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IPFE_LEDGER_GIT", "0")
    append_ledger(git_repo, "demo", 1, ROOT_HASH, BUILT_AT)

    assert len(read_ledger(git_repo, "demo")) == 1
    assert "evidence:" not in _git(git_repo, "log", "--oneline")
    assert "?? evidence-ledger/" in _git(git_repo, "status", "--porcelain")


def test_a_failing_git_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repo owned by somebody else, a hook that refuses - the line still lands."""

    def refuse(*args: object, **kwargs: object) -> object:
        raise OSError("git is not available here")

    monkeypatch.setattr(subprocess, "run", refuse)
    append_ledger(tmp_path, "demo", 1, ROOT_HASH, BUILT_AT)
    assert len(read_ledger(tmp_path, "demo")) == 1


# ---------------------------------------------------------------------------
# OpenTimestamps
# ---------------------------------------------------------------------------


def test_without_ots_the_line_says_so_and_how_to_get_it(tmp_path: Path, manifest: Path) -> None:
    append_ledger(tmp_path, "demo", 1, ROOT_HASH, BUILT_AT, manifest_path=manifest)
    entry = read_ledger(tmp_path, "demo")[0]
    assert entry["ots"] is False
    assert entry["ots_hint"] == "pip install opentimestamps-client to anchor"


def test_an_ots_binary_on_path_stamps_the_manifest(
    tmp_path: Path, manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "bin" / "ots"
    fake.parent.mkdir(parents=True)
    fake.write_text('#!/bin/sh\nprintf receipt > "$2.ots"\n', encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")

    append_ledger(tmp_path, "demo", 1, ROOT_HASH, BUILT_AT, manifest_path=manifest)

    entry = read_ledger(tmp_path, "demo")[0]
    assert entry["ots"] is True
    assert entry["ots_hint"] is None
    assert (manifest.parent / "chain-of-custody.json.ots").is_file()


def test_ots_can_be_switched_off(
    tmp_path: Path, manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IPFE_LEDGER_OTS", "0")
    append_ledger(tmp_path, "demo", 1, ROOT_HASH, BUILT_AT, manifest_path=manifest)
    assert read_ledger(tmp_path, "demo")[0]["ots"] is False
    assert not (manifest.parent / "chain-of-custody.json.ots").exists()


def test_an_ots_that_produces_nothing_is_recorded_as_a_failure(
    tmp_path: Path, manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "bin" / "ots"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")

    append_ledger(tmp_path, "demo", 1, ROOT_HASH, BUILT_AT, manifest_path=manifest)

    entry = read_ledger(tmp_path, "demo")[0]
    assert entry["ots"] is False
    assert "did not produce" in entry["ots_hint"]
