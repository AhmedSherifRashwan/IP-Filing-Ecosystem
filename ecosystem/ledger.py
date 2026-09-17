"""Anchor an evidence pack's root hash somewhere the dossier cannot reach.

A hash stored next to the files it hashes proves nothing: whoever could change
the files could recompute the hash. So every evidence pack appends one line to
`evidence-ledger/<slug>.jsonl` **in the repository**, which is version
controlled, usually pushed to a remote, and contains hashes only - never
inventor content, never a filename from outside the dossier layout, never a
title. A reader who has the repository history can see when a root hash first
appeared and that it has not moved since.

Two anchors, best effort, in increasing order of independence:

1. a git commit of that one file (`git commit -- <path>`, so a dirty tree is
   irrelevant and nothing else is ever committed). Never a push: what leaves
   this machine is the operator's decision, not a library's.
2. an OpenTimestamps attestation (`ots stamp`), if the `ots` binary is on PATH.
   That is the only part of this system that can say *when* independently of
   any clock the operator controls.

Nothing here may ever fail a run. A package that exists with an unanchored hash
is recoverable; a package that was never written because the notary was down is
not. Every failure is logged and swallowed, and `IPFE_LEDGER_GIT=0` /
`IPFE_LEDGER_OTS=0` turn either anchor off for an operator who wants neither.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

log = logging.getLogger("ipfe")

__all__ = [
    "LEDGER_DIRNAME",
    "LedgerEntry",
    "append_ledger",
    "ledger_path",
    "read_ledger",
]

LEDGER_DIRNAME: Final[str] = "evidence-ledger"

_GIT_TIMEOUT_S: Final[int] = 30
_OTS_TIMEOUT_S: Final[int] = 60
_OTS_HINT: Final[str] = "pip install opentimestamps-client to anchor"

# Escape hatches for an operator who does not want this machine touching git or
# the network. Read from the environment rather than Settings so they also work
# for anything that calls `append_ledger` directly.
_ENV_GIT: Final[str] = "IPFE_LEDGER_GIT"
_ENV_OTS: Final[str] = "IPFE_LEDGER_OTS"


class GitResult(BaseModel):
    committed: bool = False
    commit: str | None = None
    skipped: str | None = None


class LedgerEntry(BaseModel):
    """One anchored root hash. Hashes and metadata only - no dossier content.

    Deliberately says nothing about the git commit that carries it: the line is
    written before the commit, and rewriting it afterwards would leave the
    working tree dirty on every package build. Git's own history is the record
    of when the line was committed.
    """

    at: str
    matter: str
    package_version: int
    root_hash: str
    built_at: str
    evidence_dir: str | None = None
    manifest_sha256: str | None = None
    ots: bool = False
    ots_hint: str | None = None


def ledger_path(repo_root: Path | str, slug: str) -> Path:
    return Path(repo_root) / LEDGER_DIRNAME / f"{slug}.jsonl"


def read_ledger(repo_root: Path | str, slug: str) -> list[dict[str, Any]]:
    """Every well-formed line, oldest first. A corrupt line is skipped, not fatal."""
    path = ledger_path(repo_root, slug)
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            log.warning("skipping malformed ledger line in %s", path)
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries


def append_ledger(
    repo_root: Path | str,
    slug: str,
    package_version: int,
    root_hash: str,
    built_at: str,
    manifest_path: Path | str | None = None,
) -> Path:
    """Append one anchoring line and try to commit and timestamp it.

    Idempotent on content: re-anchoring the same `(package_version, root_hash)`
    that is already the last line writes nothing, so a rebuilt-but-unchanged
    package does not grow the ledger or create an empty commit.
    """
    path = ledger_path(repo_root, slug)
    manifest = Path(manifest_path) if manifest_path else None

    if _already_anchored(path, package_version, root_hash):
        log.info("ledger %s already anchors v%s %s", path, package_version, root_hash[:12])
        return path

    entry = LedgerEntry(
        at=datetime.now(UTC).isoformat(timespec="seconds"),
        matter=slug,
        package_version=package_version,
        root_hash=root_hash,
        built_at=built_at,
        evidence_dir=str(manifest.parent.name) if manifest else None,
        manifest_sha256=_sha256(manifest) if manifest else None,
    )
    _stamp(entry, manifest)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")
    except OSError as exc:
        log.error("cannot write the evidence ledger %s: %s", path, exc)
        return path

    result = _commit(Path(repo_root), path, slug, package_version, root_hash)
    if result.committed:
        log.info("evidence ledger committed: %s", result.commit or "HEAD")
    else:
        log.info("evidence ledger not committed: %s", result.skipped)
    return path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _already_anchored(path: Path, package_version: int, root_hash: str) -> bool:
    """True when the last line already anchors exactly this root hash."""
    last = _last_entry(path)
    if last is None:
        return False
    return last.get("package_version") == package_version and last.get("root_hash") == root_hash


def _last_entry(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as exc:
        log.warning("cannot read the evidence ledger %s: %s", path, exc)
        return None
    if not lines:
        return None
    try:
        parsed = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        log.warning("cannot hash %s for the ledger: %s", path, exc)
        return None


def _stamp(entry: LedgerEntry, manifest: Path | None) -> None:
    """OpenTimestamps, if it is installed and the manifest exists."""
    if _disabled(_ENV_OTS) or manifest is None or not manifest.is_file():
        entry.ots = False
        entry.ots_hint = _OTS_HINT
        return
    binary = shutil.which("ots")
    if binary is None:
        entry.ots = False
        entry.ots_hint = _OTS_HINT
        return
    result = _run([binary, "stamp", str(manifest)], cwd=manifest.parent, timeout=_OTS_TIMEOUT_S)
    stamped = result is not None and (manifest.parent / f"{manifest.name}.ots").is_file()
    entry.ots = stamped
    entry.ots_hint = None if stamped else "ots stamp did not produce a .ots receipt"


def _commit(
    repo_root: Path, path: Path, slug: str, package_version: int, root_hash: str
) -> GitResult:
    """Commit the ledger file alone. Never pushes, never touches another path."""
    if _disabled(_ENV_GIT):
        return GitResult(skipped=f"{_ENV_GIT} disables the ledger commit")
    if shutil.which("git") is None:
        return GitResult(skipped="git is not on PATH")
    if _run(["git", "rev-parse", "--git-dir"], cwd=repo_root) is None:
        return GitResult(skipped=f"{repo_root} is not a git repository")
    try:
        relative = str(path.relative_to(repo_root))
    except ValueError:
        return GitResult(skipped=f"{path} is outside {repo_root}")

    if _run(["git", "add", "--", relative], cwd=repo_root) is None:
        return GitResult(skipped="git add failed")
    message = f"evidence: {slug} v{package_version} {root_hash[:12]}"
    if _run(["git", "commit", "-m", message, "--", relative], cwd=repo_root) is None:
        return GitResult(skipped="git commit failed (nothing staged, or a hook refused it)")
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    return GitResult(committed=True, commit=(head or "").strip() or None)


def _run(argv: list[str], cwd: Path, timeout: int = _GIT_TIMEOUT_S) -> str | None:
    """Run a command, returning stdout or None. Never raises, never logs content."""
    try:
        completed = subprocess.run(  # noqa: S603 - argv is built from literals and our own paths
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("%s failed: %s", argv[0], exc)
        return None
    if completed.returncode != 0:
        log.info(
            "%s exited %s: %s",
            " ".join(argv[:2]),
            completed.returncode,
            (completed.stderr or "").strip()[:200],
        )
        return None
    return completed.stdout


def _disabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"0", "off", "false", "no"}
