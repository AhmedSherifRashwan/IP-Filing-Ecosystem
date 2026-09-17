"""The evidence pack: what was drafted, by what, from what, in what order.

A patent application drafted with model assistance will eventually be asked
awkward questions - by an examiner, by an opponent, by a court, or simply by the
next attorney on the file. The honest answer is a record, and a record is only
worth anything if it was written at the time and can be shown not to have moved
since. So at the end of every final package this module freezes one:

* `chain-of-custody.json` - a sha256 of every intake file, every phase output,
  every prompt and reply, and both packages, plus a Merkle-style
  `dossier_root_hash` over the sorted `<path>\\n<sha256>` lines. Re-hash the
  files and you can prove nothing changed; change one byte and the root moves.
* `provenance.jsonl` - one line per agent call: which agent, which model, which
  provider, which transport, how long, how big, and the hashes of the exact
  prompt and the exact reply.
* the deterministic checks (`terminology-drift.jsonl`, `numeral-cross-check.json`,
  `support-check.json`), the gate decisions, and the whole event log.

Two things this pack is explicitly *not*. It is not a timestamp: only
`ledger.py`, which anchors the root hash in a committed repo (and optionally in
OpenTimestamps), speaks to *when*. And it is not an assertion about the
invention: it records a drafting process and establishes nothing about
inventorship, priority or patentability.

Building it never blocks a package. A matter with a broken evidence pack is a
matter with a problem; a matter with no package because the evidence pack failed
is a matter with a worse one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, Field

from ecosystem.config import PHASES
from ecosystem.dossier import Dossier, sha256_file
from ecosystem.state import MatterState
from ecosystem.support_check import check_support

log = logging.getLogger("ipfe")

__all__ = [
    "EVIDENCE_DIRNAME",
    "FileRecord",
    "VerifyReport",
    "build_evidence",
    "evidence_dir_for",
    "latest_evidence_dir",
    "root_hash",
    "tool_version",
    "verify_evidence",
]

EVIDENCE_DIRNAME: Final[str] = "evidence"
MANIFEST_NAME: Final[str] = "chain-of-custody.json"

_FALLBACK_VERSION: Final[str] = "0.1.0"
_HASHED_SUFFIXES: Final[frozenset[str]] = frozenset({".md", ".json", ".jsonl"})
_RUN_FILES: Final[tuple[str, ...]] = ("prompt.md", "reply.md")
_VERSION_DIR_RE: Final[re.Pattern[str]] = re.compile(r"^v(\d+)-(\d{4}-\d{2}-\d{2})$")

_DISCLAIMER: Final[str] = (
    "This pack records the drafting process and does not establish inventorship, "
    "priority or patentability."
)


class FileRecord(BaseModel):
    """One file, as it stood when the package was built."""

    path: str
    sha256: str
    bytes: int
    mtime_utc: str


class Mismatch(BaseModel):
    path: str
    recorded: str
    actual: str


class VerifyReport(BaseModel):
    """Result of re-hashing a pack. `ok` is true only if nothing moved at all."""

    ok: bool = True
    mismatched: list[Mismatch] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    root_hash_ok: bool = True
    recorded_root_hash: str = ""
    actual_root_hash: str = ""
    checked: int = 0
    evidence_dir: str = ""

    def summary(self) -> str:
        if self.ok:
            return (
                f"{self.checked} file(s) verified; root hash {self.recorded_root_hash[:12]} holds"
            )
        parts = []
        if self.missing:
            parts.append(f"{len(self.missing)} missing")
        if self.mismatched:
            parts.append(f"{len(self.mismatched)} changed")
        if not self.root_hash_ok:
            parts.append("root hash does not match its own manifest")
        return f"{self.checked} file(s) checked: " + ", ".join(parts)


# ---------------------------------------------------------------------------
# versions and paths
# ---------------------------------------------------------------------------


def tool_version() -> str:
    """Installed distribution version, or the declared fallback.

    An evidence pack that cannot say which code produced it is much less useful,
    but a missing distribution (running from a source checkout) is not a reason
    to refuse to write one.
    """
    try:
        from importlib.metadata import version

        return version("ip-filing-ecosystem")
    except Exception:  # PackageNotFoundError, or no importlib.metadata at all
        return _FALLBACK_VERSION


def _package_version(package_dir: Path, state: MatterState) -> int:
    match = _VERSION_DIR_RE.match(package_dir.name)
    if match:
        return int(match.group(1))
    current = state.phase("PACKAGE").current_version
    return current or 1


def evidence_dir_for(dossier: Dossier, package_version: int, today: str | None = None) -> Path:
    """`evidence/v<N>-<date>` for a package version, reusing an existing one.

    Reusing matters: a rebuilt package must refresh its pack in place, or the
    ledger would anchor a root hash that no directory on disk corresponds to.
    """
    root = dossier.root / EVIDENCE_DIRNAME
    existing = sorted(root.glob(f"v{package_version}-*"))
    for candidate in existing:
        if candidate.is_dir():
            return candidate
    date = today or datetime.now(UTC).strftime("%Y-%m-%d")
    return root / f"v{package_version}-{date}"


def latest_evidence_dir(dossier: Dossier, package_version: int | None = None) -> Path | None:
    """Newest pack (optionally for one package version), or None."""
    root = dossier.root / EVIDENCE_DIRNAME
    if not root.is_dir():
        return None
    pattern = "v*-*" if package_version is None else f"v{package_version}-*"
    candidates = [
        path for path in root.glob(pattern) if path.is_dir() and (path / MANIFEST_NAME).is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=_version_sort_key)


def _version_sort_key(path: Path) -> tuple[int, str]:
    match = _VERSION_DIR_RE.match(path.name)
    return (int(match.group(1)), match.group(2)) if match else (0, path.name)


# ---------------------------------------------------------------------------
# collection and hashing
# ---------------------------------------------------------------------------


def root_hash(records: list[FileRecord]) -> str:
    """sha256 over the sorted `<path>\\n<sha256>\\n` lines.

    Deliberately simple and reproducible by hand: `sort`, `sha256sum` and `awk`
    are enough to recompute it, which is what makes it checkable by somebody who
    does not trust this code.
    """
    ordered = sorted(records, key=lambda record: record.path)
    lines = "".join(f"{record.path}\n{record.sha256}\n" for record in ordered)
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _record(path: Path, root: Path) -> FileRecord | None:
    try:
        stat = path.stat()
        digest = sha256_file(path)
    except OSError as exc:
        log.warning("cannot hash %s: %s", path, exc)
        return None
    return FileRecord(
        path=str(path.relative_to(root)),
        sha256=digest,
        bytes=stat.st_size,
        mtime_utc=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds"),
    )


def _excluded(dossier: Dossier, package_dir: Path) -> set[str]:
    """Paths deliberately left out, because they change *after* the pack is written.

    Exactly one: the package version's own `VERSION.json`, which the runner
    flips from `running` to `complete` once packaging returns. Vouching for it
    would make every healthy matter fail its own verification, and a check that
    cries wolf is a check nobody runs.
    """
    try:
        return {str((package_dir / "VERSION.json").relative_to(dossier.root))}
    except ValueError:
        return set()


def _collect_files(dossier: Dossier, package_dir: Path) -> list[FileRecord]:
    """Every file whose content the pack vouches for, deduped and sorted by path."""
    candidates: list[Path] = []

    if dossier.intake_dir.is_dir():
        candidates += [p for p in dossier.intake_dir.rglob("*") if _is_evidence_file(p)]

    for dirname in PHASES.values():
        phase_dir = dossier.root / dirname
        if not phase_dir.is_dir():
            continue
        for version_dir in phase_dir.iterdir():
            if not (version_dir.is_dir() and _VERSION_DIR_RE.match(version_dir.name)):
                continue
            candidates += [
                p
                for p in version_dir.rglob("*")
                if _is_evidence_file(p) and p.suffix in _HASHED_SUFFIXES
            ]

    if dossier.runs_dir.is_dir():
        for run_dir in dossier.runs_dir.iterdir():
            if run_dir.is_dir():
                candidates += [run_dir / name for name in _RUN_FILES if (run_dir / name).is_file()]

    # Packages built outside the dossier tree (a caller passing an explicit
    # directory) would otherwise not be vouched for at all.
    if package_dir.is_dir():
        candidates += [p for p in package_dir.rglob("*") if _is_evidence_file(p)]

    skip = _excluded(dossier, package_dir)
    records: dict[str, FileRecord] = {}
    for path in candidates:
        try:
            relative = str(path.relative_to(dossier.root))
        except ValueError:
            continue
        if relative in records or relative in skip:
            continue
        if relative.startswith(f"{EVIDENCE_DIRNAME}/"):
            continue
        record = _record(path, dossier.root)
        if record is not None:
            records[relative] = record
    return [records[key] for key in sorted(records)]


def _is_evidence_file(path: Path) -> bool:
    return path.is_file() and not path.name.startswith(".")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def build_evidence(dossier: Dossier, state: MatterState, package_dir: Path | str) -> Path:
    """Write the evidence pack for one final package and return its directory."""
    target_package = Path(package_dir)
    package_version = _package_version(target_package, state)
    built_at = datetime.now(UTC).isoformat(timespec="seconds")

    records = _collect_files(dossier, target_package)
    digest = root_hash(records)

    directory = evidence_dir_for(dossier, package_version)
    directory.mkdir(parents=True, exist_ok=True)

    manifest = {
        "built_at": built_at,
        "matter": dossier.slug,
        "title": state.matter.title,
        "package_version": package_version,
        "tool_version": tool_version(),
        "openclaw_version": _openclaw_version(state),
        "hash": "sha256",
        "file_count": len(records),
        "excluded": sorted(_excluded(dossier, target_package)),
        "excluded_reason": (
            "the package version's VERSION.json is completed by the runner after "
            "this pack is written"
        ),
        "dossier_root_hash": digest,
        "root_hash_recipe": (
            "sha256 over the concatenation of '<path>\\n<sha256>\\n' for every "
            "file below, sorted by path"
        ),
        "records": [record.model_dump() for record in records],
    }
    _write_json(directory / MANIFEST_NAME, manifest)

    _write_provenance(dossier, state, directory / "provenance.jsonl")
    _write_json(
        directory / "gate-decisions.json",
        {key: gate.model_dump(mode="json") for key, gate in state.gates.items()},
    )
    _copy(dossier.drift_path, directory / "terminology-drift.jsonl")
    _copy(dossier.crosscheck_path, directory / "numeral-cross-check.json")
    _copy(dossier.log_path, directory / "events.jsonl")
    _write_support_check(target_package, directory / "support-check.json")

    (directory / "README.md").write_text(_readme(dossier, package_version), encoding="utf-8")
    (directory / "EVIDENCE.md").write_text(
        _summary(dossier, state, package_version, digest, built_at, records), encoding="utf-8"
    )
    log.info("evidence pack %s (root %s)", directory, digest[:12])
    return directory


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _copy(source: Path, target: Path) -> bool:
    """Copy a deterministic-check artefact into the pack. Absence is not an error."""
    if not source.is_file():
        return False
    try:
        target.write_bytes(source.read_bytes())
    except OSError as exc:
        log.warning("cannot copy %s into the evidence pack: %s", source, exc)
        return False
    return True


def _write_provenance(dossier: Dossier, state: MatterState, target: Path) -> int:
    """One line per agent call, with the hash of what was actually sent and received."""
    lines: list[str] = []
    for run in state.agent_runs:
        run_dir = dossier.runs_dir / run.call_id
        entry = {
            "call_id": run.call_id,
            "phase": run.phase,
            "prompt": run.prompt,
            "agent_id": run.agent_id,
            "model": run.model,
            "provider": run.provider,
            "transport": run.transport,
            "run_id": run.run_id,
            "session_key": run.session_key,
            "idempotency_key": run.idempotency_key,
            "prompt_sha256": _sha_if_present(run_dir / "prompt.md"),
            "reply_sha256": _sha_if_present(run_dir / "reply.md"),
            "prompt_chars": run.prompt_chars,
            "reply_chars": run.reply_chars,
            "duration_ms": run.duration_ms,
            "attempt": run.attempt,
            "retry": run.retry,
            "status": run.status,
            "error": run.error,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        }
        lines.append(json.dumps(entry, ensure_ascii=False))
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def _sha_if_present(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return sha256_file(path)
    except OSError as exc:  # pragma: no cover - unreadable file mid-build
        log.warning("cannot hash %s: %s", path, exc)
        return None


def _write_support_check(package_dir: Path, target: Path) -> bool:
    description = package_dir / "description.md"
    claims = package_dir / "claims.md"
    if not (description.is_file() and claims.is_file()):
        log.info("no description/claims in %s; support check not included", package_dir)
        return False
    report = check_support(
        description.read_text(encoding="utf-8"), claims.read_text(encoding="utf-8")
    )
    target.write_text(report.to_json() + "\n", encoding="utf-8")
    return True


def _openclaw_version(state: MatterState) -> str | None:
    """The OpenClaw version, if anything in state recorded one."""
    direct = getattr(state, "openclaw_version", None)
    if isinstance(direct, str) and direct:
        return direct
    extra = (state.model_extra or {}).get("openclaw_version")
    if isinstance(extra, str) and extra:
        return extra
    for run in reversed(state.agent_runs):
        value = (run.model_extra or {}).get("openclaw_version")
        if isinstance(value, str) and value:
            return value
    return None


# ---------------------------------------------------------------------------
# human-readable halves
# ---------------------------------------------------------------------------


_README_ROWS: Final[tuple[tuple[str, str], ...]] = (
    (
        MANIFEST_NAME,
        "the sha256 of every intake file, phase output, prompt, reply and package "
        "member at the moment the package was built, plus a `dossier_root_hash` "
        "over all of them.",
    ),
    (
        "provenance.jsonl",
        "one line per agent call: agent, model, provider, transport, session and "
        "idempotency keys, prompt and reply hashes, sizes, duration, attempt, outcome.",
    ),
    (
        "gate-decisions.json",
        "every human gate: status, decision, who decided it, when. "
        "`auto-unattended` means no human did.",
    ),
    (
        "terminology-drift.jsonl",
        "every term an agent introduced outside the locked table, with its declared reason.",
    ),
    (
        "numeral-cross-check.json",
        "the deterministic reference-numeral cross-check of the finished text.",
    ),
    (
        "support-check.json",
        "the deterministic claim-support heuristic over the packaged description and "
        "claims. It complements, and never replaces, the independent reviewer's "
        "Prompt 28 support matrix.",
    ),
    ("events.jsonl", "the whole append-only event log of the run."),
    (
        "EVIDENCE.md",
        "the same facts as prose, for a reader who is not going to parse JSON.",
    ),
)


def _readme(dossier: Dossier, package_version: int) -> str:
    """What each file proves, and how to check it without trusting this code."""
    lines = [
        f"# Evidence pack - {dossier.slug} package v{package_version}",
        "",
        "Written automatically when the final package was built. Every file here is",
        "derived from the dossier; none of it is a new assertion about the invention.",
        "",
        "| file | what it proves |",
        "| --- | --- |",
    ]
    lines += [f"| `{name}` | {description} |" for name, description in _README_ROWS]
    lines += [
        "",
        "## Verify it",
        "",
        "```",
        f"uv run ipfe evidence --matter {dossier.slug} --verify",
        "```",
        "",
        f"That re-hashes every file listed in `{MANIFEST_NAME}`, reports anything",
        "missing or changed, and recomputes the root hash. By hand, from the dossier",
        "root, `sha256sum <path>` reproduces any single record; the root hash is the",
        "sha256 of the concatenation of `<path>\\n<sha256>\\n` for every record,",
        "sorted by path.",
        "",
        "## What this is not",
        "",
        _DISCLAIMER,
        "",
        "It is not a timestamp either: the root hash is anchored outside the dossier",
        f"by `evidence-ledger/{dossier.slug}.jsonl` in the repository and, when",
        f"OpenTimestamps is available, by `{MANIFEST_NAME}.ots`.",
    ]
    return "\n".join(lines) + "\n"


def _summary(
    dossier: Dossier,
    state: MatterState,
    package_version: int,
    digest: str,
    built_at: str,
    records: list[FileRecord],
) -> str:
    by_agent: dict[str, int] = {}
    by_model: dict[str, int] = {}
    failed = 0
    for run in state.agent_runs:
        by_agent[run.agent_id] = by_agent.get(run.agent_id, 0) + 1
        by_model[run.model or "unknown"] = by_model.get(run.model or "unknown", 0) + 1
        if run.status not in {"ok", "complete", "finished"}:
            failed += 1

    lines = [
        f"# Evidence summary - {dossier.slug}",
        "",
        f"**{state.matter.title}**  ",
        f"Jurisdictions: {', '.join(state.matter.jurisdictions) or 'not stated'}  ",
        f"Package version: v{package_version}  ",
        f"Root hash: `{digest}`  ",
        f"Tool: ip-filing-ecosystem {tool_version()}"
        + (f", OpenClaw {_openclaw_version(state)}" if _openclaw_version(state) else ""),
        "",
        "## Dates",
        "",
        "| what | when (UTC) |",
        "| --- | --- |",
        f"| matter created | {state.created_at} |",
    ]
    for phase in PHASES:
        window = _phase_window(state, phase)
        if window:
            lines.append(f"| {phase} first call | {window[0]} |")
            lines.append(f"| {phase} last call | {window[1]} |")
    lines += [
        f"| package built | {built_at} |",
        "",
        "## Calls",
        "",
        f"{len(state.agent_runs)} agent call(s) over {len(records)} recorded file(s); "
        f"{state.counters.get('contract_corrections', 0)} contract correction(s), "
        f"{state.counters.get('retries', 0)} retr(ies), {failed} call(s) not finishing `ok`.",
        "",
        "| agent | calls |",
        "| --- | --- |",
    ]
    # Two tables, not two pairs of columns: the same call is counted in both,
    # and putting them side by side would imply a row-by-row relationship that
    # does not exist (one agent can be served by more than one model).
    lines += [f"| {agent} | {count} |" for agent, count in sorted(by_agent.items())]
    lines += ["", "| model | calls |", "| --- | --- |"]
    lines += [f"| {model} | {count} |" for model, count in sorted(by_model.items())]

    lines += [
        "",
        "## Terminology",
        "",
        f"{state.terminology.terms} term(s) locked"
        + (f" at {state.terminology.locked_at}" if state.terminology.locked_at else "")
        + f"; {state.terminology.violations_blocked} violation(s) blocked, "
        f"{state.terminology.drift_logged} drift record(s) logged.",
        "",
        "## Gates",
        "",
        "| gate | status | decision | decided by | at |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key, gate in state.gates.items():
        lines.append(
            f"| {key} | {gate.status} | {gate.decision or '-'} | {gate.decided_by or '-'} "
            f"| {gate.decided_at or '-'} |"
        )
    auto = [
        key for key, gate in state.gates.items() if (gate.decided_by or "") == "auto-unattended"
    ]
    if auto:
        lines += [
            "",
            f"> Gates {', '.join(auto)} were accepted automatically in unattended mode. "
            "No human confirmed what they accepted.",
        ]

    lines += [
        "",
        "## What this pack is not",
        "",
        _DISCLAIMER,
        "",
        f"Verify with `uv run ipfe evidence --matter {dossier.slug} --verify`.",
    ]
    return "\n".join(lines) + "\n"


def _phase_window(state: MatterState, phase: str) -> tuple[str, str] | None:
    stamps = [run.started_at for run in state.agent_runs if run.phase == phase and run.started_at]
    if not stamps:
        return None
    ends = [
        run.finished_at or run.started_at
        for run in state.agent_runs
        if run.phase == phase and (run.finished_at or run.started_at)
    ]
    return min(stamps), max(ends or stamps)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def verify_evidence(dossier: Dossier, evidence_dir: Path | str) -> VerifyReport:
    """Re-hash every file the pack lists and recompute the root.

    Three separable failures, reported separately because they mean different
    things: a *missing* file means the dossier was pruned, a *mismatched* file
    means content changed after the package was built, and a bad *root hash*
    means the manifest itself was edited.
    """
    directory = Path(evidence_dir)
    manifest_path = directory / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"no evidence manifest at {manifest_path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{manifest_path} is not valid JSON: {exc}") from exc

    recorded = [FileRecord.model_validate(item) for item in manifest.get("records", [])]
    report = VerifyReport(
        recorded_root_hash=str(manifest.get("dossier_root_hash", "")),
        checked=len(recorded),
        evidence_dir=str(directory),
    )

    actual: list[FileRecord] = []
    for record in recorded:
        path = dossier.root / record.path
        if not path.is_file():
            report.missing.append(record.path)
            continue
        digest = sha256_file(path)
        actual.append(record.model_copy(update={"sha256": digest}))
        if digest != record.sha256:
            report.mismatched.append(
                Mismatch(path=record.path, recorded=record.sha256, actual=digest)
            )

    report.actual_root_hash = root_hash(actual)
    report.root_hash_ok = root_hash(recorded) == report.recorded_root_hash
    report.ok = not report.mismatched and not report.missing and report.root_hash_ok
    return report
