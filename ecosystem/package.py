"""The two packages: what the blind reviewer sees, and what counsel receives.

`build_review_package()` is the structural half of Agent 7's independence. The
reviewer is given finished text and nothing else - no drafting rationale, no
envelopes, no frontmatter, no phase outputs - and this module *asserts* that,
because a blindness guarantee that is only a convention is not a guarantee. A
package that contains a `## Rationale` heading, a fenced envelope or YAML
frontmatter raises `BlindnessViolation` and the run stops.

`build_final_package()` assembles what a human hands to qualified patent
counsel. It is deliberately dull: copy the reviewed text, copy the reviewer's
matrices verbatim, list every residual finding including the ones nobody fixed,
and never imply that anything here clears the application for filing. It ends by
freezing an evidence pack (`evidence.py`) and anchoring its root hash
(`ledger.py`); both are best-effort and neither may ever block a package.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ecosystem import evidence as evidence_module
from ecosystem import ledger as ledger_module
from ecosystem import report
from ecosystem import state as state_module
from ecosystem.calls import section_body
from ecosystem.contracts import Finding, deliverable_only
from ecosystem.dossier import Dossier, sha256_text, split_frontmatter
from ecosystem.export import export_markdown
from ecosystem.loopback import SEVERE, load_findings
from ecosystem.state import MatterState
from ecosystem.support_check import check_support

log = logging.getLogger("ipfe")

__all__ = [
    "BlindnessViolation",
    "PackageError",
    "REVIEW_MEMBERS",
    "anchor_evidence",
    "anchor_root_hash",
    "build_final_package",
    "build_review_package",
    "write_cover_memo",
]

# Order is the order a reader should read them in; it is also the MANIFEST order.
REVIEW_MEMBERS: Final[tuple[str, ...]] = (
    "description.md",
    "figures.md",
    "claims.md",
    "registry.json",
    "abstract.md",
)

_RATIONALE_RE: Final[re.Pattern[str]] = re.compile(r"^##\s+Rationale\s*$", re.MULTILINE)
_ENVELOPE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*```json", re.MULTILINE)
_FRONTMATTER_RE: Final[re.Pattern[str]] = re.compile(r"\A---\r?\n")

_CONTACT_SENTENCE: Final[str] = (
    "No agent in this system may authorise filing; this package is a drafting aid "
    "and requires review and sign-off by qualified patent counsel before any "
    "filing decision."
)


class PackageError(RuntimeError):
    """The package cannot be built: a member is missing or unreadable."""


class BlindnessViolation(PackageError):
    """Drafting rationale reached the reviewer's package. Never recoverable in place."""

    def __init__(self, member: str, what: str) -> None:
        super().__init__(
            f"{member} contains {what}; the independent reviewer must receive "
            "finished text only. Fix the write-back that produced it - do not "
            "strip it here, or the next build will leak it again."
        )
        self.member = member
        self.what = what


# ---------------------------------------------------------------------------
# review package
# ---------------------------------------------------------------------------


def build_review_package(
    dossier: Dossier,
    state: MatterState,
    version: int | None = None,
) -> Path:
    """Assemble `06-review/vN/package/` and prove it is rationale-free."""
    review_version = version or state.phase("REVIEW").current_version
    if review_version is None:
        raise PackageError("no REVIEW version is open; call dossier.new_version('REVIEW', ...)")
    version_dir = dossier.version("REVIEW", review_version)
    if version_dir is None:
        raise PackageError(f"REVIEW v{review_version} does not exist")

    package_dir = version_dir.path / "package"
    package_dir.mkdir(parents=True, exist_ok=True)

    members: list[dict[str, Any]] = []
    for name, text, source in _review_members(dossier):
        if name.endswith(".md"):
            _assert_blind(name, text)
        path = package_dir / name
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        members.append(
            {
                "name": name,
                "source": source,
                "sha256": sha256_text(path.read_text(encoding="utf-8")),
                "chars": len(text),
            }
        )

    manifest = {
        "built_at": _now(),
        "matter": dossier.slug,
        "review_version": review_version,
        "contains": "finished filing text only: no drafting rationale, no envelopes",
        "members": members,
    }
    (package_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return package_dir


def _review_members(dossier: Dossier) -> list[tuple[str, str, str]]:
    """`(filename, text, source)` for every member that exists."""
    out: list[tuple[str, str, str]] = []

    description, source = _derived(dossier, "P4", "description.md")
    out.append(("description.md", description, source))

    figures, figure_sources = _figures(dossier)
    out.append(("figures.md", figures, figure_sources))

    claims, source = _derived(dossier, "P5", "claims.md")
    out.append(("claims.md", claims, source))

    if not dossier.registry_path.is_file():
        raise PackageError(
            f"{dossier.registry_path} does not exist; Prompt 20a must run before the review"
        )
    out.append(
        (
            "registry.json",
            dossier.registry_path.read_text(encoding="utf-8"),
            str(dossier.registry_path.relative_to(dossier.root)),
        )
    )

    abstract = _previous_abstract(dossier)
    if abstract is not None:
        out.append(("abstract.md", abstract[0], abstract[1]))
    return out


def _derived(dossier: Dossier, phase: str, filename: str) -> tuple[str, str]:
    """Newest `description.md` / `claims.md`, from the running version if there is one."""
    for version_dir in reversed(dossier.versions(phase)):
        candidate = version_dir.path / filename
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8")
            return text, str(candidate.relative_to(dossier.root))
    raise PackageError(
        f"no {filename} in any {phase} version; the inner loop has not produced it yet"
    )


def _figures(dossier: Dossier) -> tuple[str, str]:
    """Figure instructions (20a) followed by the figure plan (20).

    The reviewer judges the drawings as an examiner would: from the instructions
    a draftsperson would execute plus the plan that says what each figure is
    for. There are no images in this pipeline and pretending otherwise would be
    worse than saying so.
    """
    sources: list[str] = []
    parts: list[str] = []

    registry_output = dossier.latest_output("P4", "20a")
    if registry_output is not None:
        instructions = section_body(deliverable_only(_body(registry_output)), "Figure Instructions")
        if instructions:
            parts += ["## Figure Instructions", "", instructions]
            sources.append(str(registry_output.relative_to(dossier.root)))

    plan_output = dossier.latest_output("P4", "20")
    if plan_output is not None:
        plan = deliverable_only(_body(plan_output))
        if plan:
            parts += ["", "## Figure Plan", "", plan]
            sources.append(str(plan_output.relative_to(dossier.root)))

    if not parts:
        raise PackageError("no figure material: neither Prompt 20 nor 20a produced a Deliverable")
    return "\n".join(parts), ", ".join(sources)


def _previous_abstract(dossier: Dossier) -> tuple[str, str] | None:
    """Carry an abstract forward into a later review round once 39a has run."""
    for version_dir in reversed(dossier.versions("REVIEW")):
        candidate = version_dir.path / "package" / "abstract.md"
        if candidate.is_file():
            return (
                candidate.read_text(encoding="utf-8"),
                str(candidate.relative_to(dossier.root)),
            )
    return None


def _body(path: Path) -> str:
    _, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return body


def _assert_blind(member: str, text: str) -> None:
    if _FRONTMATTER_RE.match(text):
        raise BlindnessViolation(member, "YAML frontmatter")
    if _RATIONALE_RE.search(text):
        raise BlindnessViolation(member, "a `## Rationale` section")
    if _ENVELOPE_RE.search(text):
        raise BlindnessViolation(member, "a fenced json envelope")


# ---------------------------------------------------------------------------
# final package
# ---------------------------------------------------------------------------


def build_final_package(
    dossier: Dossier,
    state: MatterState,
    version: int | None = None,
    cover_memo: str | None = None,
) -> Path:
    """Assemble `07-package/vN/` from the reviewed package and the review outputs."""
    package_version = version or state.phase("PACKAGE").current_version
    if package_version is None:
        raise PackageError("no PACKAGE version is open")
    version_dir = dossier.version("PACKAGE", package_version)
    if version_dir is None:
        raise PackageError(f"PACKAGE v{package_version} does not exist")
    target = version_dir.path

    review = dossier.latest("REVIEW") or _newest_review(dossier)
    if review is None:
        raise PackageError("no REVIEW version to package from")
    review_package = review.path / "package"
    if not review_package.is_dir():
        raise PackageError(f"{review_package} does not exist")

    members: list[dict[str, Any]] = []

    for name in REVIEW_MEMBERS:
        source = review_package / name
        if not source.is_file():
            if name == "abstract.md":
                log.warning("no abstract in %s; packaging without it", review_package)
                continue
            raise PackageError(f"{source} is missing from the reviewed package")
        members.append(_copy(source, target / name, dossier))

    for filename, prompt_id, title in (
        ("support-matrix.md", "28", "Claim-to-specification support matrix"),
        ("priority-matrix.md", "31", "Priority entitlement and new-matter control"),
        ("filing-checklist.md", "39", "Filing checklist"),
    ):
        member = _from_review_output(dossier, review.path, prompt_id, title, target / filename)
        if member is not None:
            members.append(member)

    support = _support_check(dossier, target)
    if support is not None:
        members.append(support)

    members.append(_gate14(dossier, review.path, target / "gate-14.md"))
    members.append(_residuals(dossier, state, review.path, target / "residual-findings.md"))
    members.append(_gate_decisions(dossier, state, target / "gate-decisions.md"))
    try:
        members.append(
            report.build_findings_report(dossier, state, review.path, target / "findings-report.md")
        )
    except Exception as exc:  # a report is never a reason to lose the package
        log.warning("findings report could not be built: %s", exc)
    members.append(
        _write(
            target / "cover-memo.md",
            cover_memo or _fallback_memo(dossier, state),
            "x-package-memo" if cover_memo else "runner fallback",
            dossier,
        )
    )

    members.append(_evidence_member(dossier, package_version))

    manifest = {
        "built_at": _now(),
        "matter": dossier.slug,
        "title": state.matter.title,
        "jurisdictions": state.matter.jurisdictions,
        "package_version": package_version,
        "review_version": review.v,
        "requires": _CONTACT_SENTENCE,
        "members": members,
    }
    manifest["readable"] = export_readable(target, state)
    (target / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Last, so the pack hashes the finished manifest as well as the members.
    anchor_evidence(dossier, state, target)
    return target


# Documents a person actually opens, in the order they should read them.
READABLE: Final[tuple[tuple[str, str], ...]] = (
    ("findings-report.md", "Independent review findings"),
    ("cover-memo.md", "Cover memo for counsel"),
    ("abstract.md", "Abstract"),
    ("claims.md", "Claims"),
    ("description.md", "Description"),
    ("figures.md", "Figures and reference numerals"),
    ("filing-checklist.md", "Filing checklist"),
    ("support-matrix.md", "Claim-to-specification support matrix"),
    ("gate-14.md", "Fourteen-question filing gate"),
    ("residual-findings.md", "Residual findings"),
)


def export_readable(target: Path, state: MatterState) -> dict[str, list[str]]:
    """Render the package as Word and PDF beside the markdown, in `readable/`.

    The markdown is the record; these are what gets read and sent. A format
    that fails to render is logged and skipped, never fatal: a missing PDF must
    not cost anybody the package it was made from.
    """
    written: dict[str, list[str]] = {"word": [], "pdf": []}
    prefix = state.matter.title or ""
    for name, label in READABLE:
        source = target / name
        if not source.is_file():
            continue
        title = f"{prefix} — {label}" if prefix else label
        for path in export_markdown(source, target / "readable", title=title):
            written["pdf" if path.suffix == ".pdf" else "word"].append(path.name)
    log.info("readable exports: %d Word, %d PDF", len(written["word"]), len(written["pdf"]))
    return written


def write_cover_memo(dossier: Dossier, package_dir: Path, text: str) -> Path:
    """Replace the fallback memo once patentcounsel has written the real one."""
    path = package_dir / "cover-memo.md"
    body = text.rstrip() + "\n"
    if _CONTACT_SENTENCE not in body:
        # The memo template requires this sentence verbatim. If the model
        # dropped it, the runner puts it back rather than shipping a package
        # that reads as though an agent had cleared it.
        body += f"\n{_CONTACT_SENTENCE}\n"
    path.write_text(body, encoding="utf-8")
    manifest_path = package_dir / "MANIFEST.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for member in manifest.get("members", []):
            if member.get("name") == "cover-memo.md":
                member["source"] = "x-package-memo"
                member["sha256"] = sha256_text(body)
                member["chars"] = len(body)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    # The memo and the manifest have both just changed, so the evidence pack
    # built at the end of `build_final_package` now describes files that no
    # longer exist in that form. Re-freeze it rather than ship a pack whose own
    # verification fails.
    _refresh_evidence(dossier, package_dir)
    return path


# ---------------------------------------------------------------------------
# support check and evidence pack
# ---------------------------------------------------------------------------


def _support_check(dossier: Dossier, target: Path) -> dict[str, Any] | None:
    """Deterministic claim-support table over the text this package actually carries.

    Run on the packaged copies, not on the phase outputs: the point is to say
    something about the bytes counsel receives. It is advisory - an unsupported
    row is a question for a human, never a reason to withhold the package - and
    it never replaces the reviewer's Prompt 28 matrix, which is packaged beside
    it.
    """
    description = target / "description.md"
    claims = target / "claims.md"
    if not (description.is_file() and claims.is_file()):
        log.warning("no description/claims in %s; support check not packaged", target)
        return None
    try:
        report = check_support(
            description.read_text(encoding="utf-8"), claims.read_text(encoding="utf-8")
        )
    except Exception as exc:  # deliberately broad: an advisory check cannot block
        log.warning("support check failed (%s); packaging without it", exc)
        return None
    member = _write(
        target / "support-check.md",
        report.to_markdown("Claim support check (deterministic, advisory)"),
        "ecosystem/support_check.py over description.md + claims.md",
        dossier,
    )
    member["unsupported_limitations"] = report.unsupported_count
    return member


def _evidence_member(dossier: Dossier, package_version: int) -> dict[str, Any]:
    """The MANIFEST entry pointing at the pack. The pack carries its own hashes."""
    directory = evidence_module.evidence_dir_for(dossier, package_version)
    return {
        "name": "evidence",
        "source": "ecosystem/evidence.py",
        "path": str(directory.relative_to(dossier.root)),
        "sha256": "",
        "chars": 0,
        "note": (
            "chain of custody for this package: sha256 of every intake file, phase "
            f"output, prompt, reply and member, plus the root hash. Verify with "
            f"`ipfe evidence --matter {dossier.slug} --verify`."
        ),
    }


def anchor_evidence(dossier: Dossier, state: MatterState, target: Path) -> Path | None:
    """Freeze the evidence pack and anchor its root hash. Never raises.

    Packaging is the point of the run; evidence is what makes the package
    defensible later. If the second fails, the first still has to be delivered -
    loudly, in the log, but delivered.
    """
    if not _enabled(dossier, "evidence_pack", "IPFE_EVIDENCE"):
        log.info("evidence pack disabled by configuration")
        return None
    try:
        directory = evidence_module.build_evidence(dossier, state, target)
    except Exception as exc:  # deliberately broad: evidence never blocks a package
        log.warning("evidence pack failed for %s: %s", target, exc)
        return None
    try:
        anchor_root_hash(dossier, directory)
    except Exception as exc:  # deliberately broad: so does anchoring
        log.warning("evidence ledger failed for %s: %s", directory, exc)
    return directory


def anchor_root_hash(dossier: Dossier, directory: Path) -> None:
    """Append the pack's root hash to the repository ledger, if the repo owns it.

    A dossier outside the repository (a test, or a relocated `IPFE_DOSSIERS_DIR`)
    is deliberately not anchored: the ledger's whole value is that it is
    committed alongside the code that produced it, and writing into somebody
    else's checkout would be a surprise.
    """
    if not _enabled(dossier, "evidence_ledger", "IPFE_LEDGER"):
        log.info("evidence ledger disabled by configuration")
        return
    repo_root = Path(getattr(dossier.settings, "repo_root", dossier.root)).resolve()
    try:
        dossier.root.resolve().relative_to(repo_root)
    except ValueError:
        log.info("dossier %s is outside %s; not anchoring in the ledger", dossier.root, repo_root)
        return
    manifest_path = directory / evidence_module.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ledger_module.append_ledger(
        repo_root,
        dossier.slug,
        int(manifest.get("package_version") or 0),
        str(manifest.get("dossier_root_hash") or ""),
        str(manifest.get("built_at") or _now()),
        manifest_path=manifest_path,
    )


def _refresh_evidence(dossier: Dossier, package_dir: Path) -> None:
    """Rebuild the pack for a package whose contents changed after it was built."""
    if not (package_dir / "MANIFEST.json").is_file():
        return
    try:
        state = state_module.load(dossier.state_path)
    except Exception as exc:  # deliberately broad: a refresh is best effort
        log.warning("cannot reload state to refresh the evidence pack: %s", exc)
        return
    anchor_evidence(dossier, state, package_dir)


def _enabled(dossier: Dossier, setting: str, env: str) -> bool:
    """Config first (the other half of the runner may grow the flag), env second."""
    if not bool(getattr(dossier.settings, setting, True)):
        return False
    return os.environ.get(env, "").strip().lower() not in {"0", "off", "false", "no"}


def _newest_review(dossier: Dossier) -> Any:
    versions = dossier.versions("REVIEW")
    return versions[-1] if versions else None


def _copy(source: Path, target: Path, dossier: Dossier) -> dict[str, Any]:
    text = source.read_text(encoding="utf-8")
    return _write(target, text, str(source.relative_to(dossier.root)), dossier)


def _write(target: Path, text: str, source: str, dossier: Dossier) -> dict[str, Any]:
    body = text.rstrip() + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return {
        "name": target.name,
        "source": source,
        "sha256": sha256_text(body),
        "chars": len(body),
    }


def _from_review_output(
    dossier: Dossier,
    review_dir: Path,
    prompt_id: str,
    title: str,
    target: Path,
) -> dict[str, Any] | None:
    path = _find_output(review_dir, prompt_id)
    if path is None:
        log.warning(
            "no Prompt %s output in %s; %s not packaged", prompt_id, review_dir, target.name
        )
        return None
    body = deliverable_only(_body(path)) or _body(path)
    # The reviewer's H2 is `## Findings`; the package presents it under its own
    # title so a reader is not told that a support matrix is a finding.
    text = f"# {title}\n\n*From Prompt {prompt_id}, independent reviewer.*\n\n{body}"
    return _write(target, text, str(path.relative_to(dossier.root)), dossier)


def _find_output(review_dir: Path, prompt_id: str) -> Path | None:
    exact = review_dir / f"{prompt_id}.md"
    if exact.is_file():
        return exact
    matches = sorted(review_dir.glob(f"{prompt_id}-*.md"))
    return matches[0] if matches else None


def _gate14(dossier: Dossier, review_dir: Path, target: Path) -> dict[str, Any]:
    """The fourteen answers as a table, from the envelope rather than the prose.

    The envelope is the structured record; reproducing it means a `no` cannot be
    softened by how the prose was phrased.
    """
    envelope_path = review_dir / "gate14.envelope.json"
    lines = ["# Fourteen-question filing gate", "", "*Answers from the independent reviewer.*", ""]
    source = str(envelope_path.relative_to(dossier.root)) if envelope_path.is_file() else "missing"
    if envelope_path.is_file():
        data = json.loads(envelope_path.read_text(encoding="utf-8"))
        answers = data.get("gate14") or []
        lines += ["| Q | Answer | Evidence |", "| --- | --- | --- |"]
        for answer in answers:
            evidence = str(answer.get("evidence", "")).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {answer.get('q')} | {answer.get('answer')} | {evidence} |")
        nos = [a for a in answers if a.get("answer") != "yes"]
        lines += [
            "",
            f"**{len(nos)} of {len(answers)} questions are not a clear `yes`.**"
            if nos
            else "**All fourteen questions answered `yes`.**",
        ]
    else:
        lines.append("The fourteen-question gate has not been run for this package.")
    return _write(target, "\n".join(lines), source, dossier)


def _gate_decisions(dossier: Dossier, state: MatterState, target: Path) -> dict[str, Any]:
    """Who decided each gate. An unattended run says so here, in the package itself."""
    lines = [
        "# Gate decisions",
        "",
        "| gate | status | decision | by | at | note |",
        "|---|---|---|---|---|---|",
    ]
    for key, gate in state.gates.items():
        lines.append(
            f"| {key} | {gate.status} | {gate.decision or '-'} | {gate.decided_by or '-'} | "
            f"{gate.decided_at or '-'} | {gate.note or ''} |"
        )
    auto = [k for k, g in state.gates.items() if (g.decided_by or "") == "auto-unattended"]
    if auto:
        lines += [
            "",
            f"> Gates {', '.join(auto)} were accepted automatically in unattended mode. "
            "Nothing they accepted has been confirmed by a human; treat every embodiment, "
            "answer and residual finding as candidate until counsel reviews it.",
        ]
    return _write(target, "\n".join(lines) + "\n", "state.json", dossier)


def _residuals(
    dossier: Dossier, state: MatterState, review_dir: Path, target: Path
) -> dict[str, Any]:
    """Every finding the package still carries, including accepted critical ones."""
    findings = load_findings(review_dir)
    accepted = {
        decision.category
        for decision in state.loopback.forced_decisions
        if decision.decision in {"accept-residual", "narrow", "manual-fix"}
    }
    residual = [f for f in findings if f.severity not in SEVERE]
    forced = [f for f in findings if f.severity in SEVERE and f.category in accepted]
    unresolved = [f for f in findings if f.severity in SEVERE and f.category not in accepted]

    lines = [
        "# Residual findings",
        "",
        "Findings the independent reviewers raised that are still open in this "
        "package. Nothing here has been fixed; each is for counsel to weigh.",
        "",
        "The reviewer-agreement column says how much weight the finding carries: "
        "`consensus` means both reviewers, on different model families and blind "
        "to each other, raised it separately. A single-reviewer finding is one "
        "model's reading, and a single-reviewer high is a lead the runner "
        "deliberately did not spend a loop-back cycle on.",
    ]
    lines += _finding_table("Accepted at a loop-back cap decision (critical/high)", forced)
    lines += _finding_table("Still open (critical/high)", unresolved)
    lines += _finding_table("Reported, not routed (medium/low)", residual)

    for decision in state.loopback.forced_decisions:
        lines += [
            "",
            f"> Loop-back cap on **{decision.category}** decided `{decision.decision}` "
            f"by {decision.decided_by} at {decision.at}."
            + (f" Note: {decision.note}" if decision.note else ""),
        ]
    return _write(target, "\n".join(lines), str(review_dir.name) + "/findings.json", dossier)


def _finding_table(title: str, findings: list[Finding]) -> list[str]:
    if not findings:
        return ["", f"## {title}", "", "None."]
    lines = [
        "",
        f"## {title}",
        "",
        "| id | severity | category | reviewer agreement | claims / paragraphs | finding "
        "| least-narrowing action |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for finding in findings:
        refs = ", ".join([*finding.claim_refs, *finding.spec_refs]) or "-"
        lines.append(
            f"| {finding.id} | {finding.severity} | {finding.category} | "
            f"{_agreement(finding)} | {refs} | "
            f"{_cell(finding.title)} | {_cell(finding.least_narrowing_action)} |"
        )
    return lines


def _agreement(finding: Finding) -> str:
    """`consensus`, `<reviewer> only`, or `-` for a pre-two-reviewer findings.json."""
    reviewers = list(getattr(finding, "reviewers", []) or [])
    if getattr(finding, "consensus", False) or len(reviewers) > 1:
        return "consensus"
    if not reviewers:
        return "-"
    tag = f"{reviewers[0]} only"
    return f"{tag} (single-reviewer)" if finding.severity in SEVERE else tag


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _fallback_memo(dossier: Dossier, state: MatterState) -> str:
    """Used when patentcounsel could not be reached. Facts only, no reassurance."""
    return "\n".join(
        [
            "# Cover memo",
            "",
            "*(Automatic memo: the orchestrator could not be reached, so this is a "
            "factual listing rather than a written memo.)*",
            "",
            "## What is in the package",
            "",
            f"Matter `{dossier.slug}` - {state.matter.title}. "
            f"Jurisdictions: {', '.join(state.matter.jurisdictions) or 'not stated'}.",
            "",
            "See MANIFEST.json for every file, its source and its sha256.",
            "",
            "## Residual findings",
            "",
            "See `residual-findings.md`.",
            "",
            "## Fourteen-question gate",
            "",
            "See `gate-14.md`.",
            "",
            "## Counsel review required",
            "",
            _CONTACT_SENTENCE,
        ]
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
