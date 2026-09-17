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
and never imply that anything here clears the application for filing.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ecosystem.calls import section_body
from ecosystem.contracts import Finding, deliverable_only
from ecosystem.dossier import Dossier, sha256_text, split_frontmatter
from ecosystem.loopback import SEVERE, load_findings
from ecosystem.state import MatterState

log = logging.getLogger("ipfe")

__all__ = [
    "BlindnessViolation",
    "PackageError",
    "REVIEW_MEMBERS",
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
        instructions = section_body(
            deliverable_only(_body(registry_output)), "Figure Instructions"
        )
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

    members.append(_gate14(dossier, review.path, target / "gate-14.md"))
    members.append(_residuals(dossier, state, review.path, target / "residual-findings.md"))
    members.append(
        _write(
            target / "cover-memo.md",
            cover_memo or _fallback_memo(dossier, state),
            "x-package-memo" if cover_memo else "runner fallback",
            dossier,
        )
    )

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
    (target / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return target


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
    return path


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
        "Findings the independent reviewer raised that are still open in this "
        "package. Nothing here has been fixed; each is for counsel to weigh.",
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
        "| id | severity | category | claims / paragraphs | finding | least-narrowing action |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for finding in findings:
        refs = ", ".join([*finding.claim_refs, *finding.spec_refs]) or "-"
        lines.append(
            f"| {finding.id} | {finding.severity} | {finding.category} | {refs} | "
            f"{_cell(finding.title)} | {_cell(finding.least_narrowing_action)} |"
        )
    return lines


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
