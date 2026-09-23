"""Turn the reviewers' findings into a document a human can actually read.

`findings.json` is the machine record and `residual-findings.md` is a table of
everything still open; neither is something an inventor sits down with. This
builds the report: the numbers first, then every critical and high finding in
full -- what it attacks, which claim, which paragraph, who raised it and the
least narrowing way out -- then the medium and low ones in a table, and finally
an honest statement of what the review could not see.

It is deterministic. No model call is needed to produce it, so it cannot fail
the way a drafting step can, and it says the same thing every time it runs over
the same findings.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ecosystem.contracts import Finding
from ecosystem.dossier import Dossier
from ecosystem.loopback import load_findings
from ecosystem.state import MatterState

log = logging.getLogger("ipfe")

__all__ = ["build_findings_report", "findings_report_markdown"]

_SEVERITY_ORDER: Final[tuple[str, ...]] = ("critical", "high", "medium", "low")

_SEVERITY_MEANS: Final[dict[str, str]] = {
    "critical": "the claim falls as drafted, or the specification contradicts itself",
    "high": "a credible rejection that needs an amendment before filing",
    "medium": "a weakness counsel should weigh",
    "low": "tidy-up",
}

_CATEGORY_MEANS: Final[dict[str, str]] = {
    "novelty": "novelty and inventive step",
    "enablement": "enablement and written description",
    "design-around": "design-around and detectability",
    "support": "support, priority and amendment basis",
    "formal": "formal and consistency",
}


def _fmt_refs(refs: list[str]) -> str:
    return ", ".join(str(ref) for ref in refs) if refs else "—"


def _raised_by(finding: Finding) -> str:
    who = ", ".join(finding.reviewers) or "the reviewer"
    prompts = ", ".join(finding.sources or [finding.source_prompt])
    agreement = " · **both reviewers agreed**" if finding.consensus else ""
    return f"{who} (review step{'s' if len(finding.sources) > 1 else ''} {prompts}){agreement}"


def _finding_section(finding: Finding) -> list[str]:
    """One finding, in full. Nothing is summarised away."""
    return [
        f"#### {finding.id} — {finding.title}",
        "",
        f"**Severity** {finding.severity} · **Area** "
        f"{_CATEGORY_MEANS.get(finding.category, finding.category)} · "
        f"**Claims** {_fmt_refs(finding.claim_refs)} · "
        f"**Specification** {_fmt_refs(finding.spec_refs)}",
        "",
        f"**Raised by** {_raised_by(finding)}",
        "",
        f"**The objection.** {finding.attack.strip()}",
        "",
        f"**Least narrowing fix.** {finding.least_narrowing_action.strip()}"
        + (
            "  \n*This fix needs disclosure the application does not yet contain.*"
            if finding.requires_new_disclosure
            else ""
        ),
        "",
    ]


def _counts_table(findings: list[Finding]) -> list[str]:
    by_category: dict[str, Counter[str]] = defaultdict(Counter)
    for finding in findings:
        by_category[finding.category][str(finding.severity)] += 1
    lines = [
        "| Area | Critical | High | Medium | Low | Total |",
        "|---|---|---|---|---|---|",
    ]
    for category, counts in sorted(by_category.items()):
        total = sum(counts.values())
        lines.append(
            f"| {_CATEGORY_MEANS.get(category, category)} | "
            + " | ".join(str(counts.get(severity, 0)) for severity in _SEVERITY_ORDER)
            + f" | {total} |"
        )
    overall: Counter[str] = Counter(str(finding.severity) for finding in findings)
    lines.append(
        "| **All areas** | "
        + " | ".join(f"**{overall.get(severity, 0)}**" for severity in _SEVERITY_ORDER)
        + f" | **{len(findings)}** |"
    )
    return lines


def _limits(dossier: Dossier, state: MatterState, findings: list[Finding]) -> list[str]:
    """What the review could not see. Said plainly, because it bounds every finding above."""
    lines: list[str] = []
    prior_art = [
        path
        for path in (dossier.intake_dir / "prior-art").glob("*")
        if path.is_file() and not path.name.startswith("SEARCH-")
    ]
    if not prior_art:
        lines.append(
            "- **No prior art was supplied.** Every novelty and inventive-step finding below "
            "was reached from the text alone, and no reviewer could test the application "
            "against a real reference. A professional search is still required."
        )
    else:
        lines.append(
            f"- The reviewers were given {len(prior_art)} prior-art reference(s). A "
            "professional search is still required before filing."
        )
    if not list((dossier.intake_dir / "priority").glob("*")):
        lines.append(
            "- **No priority document was supplied**, so priority entitlement could not be "
            "assessed; the priority matrix in the package says so as well."
        )
    auto = [
        key for key, gate in state.gates.items() if (gate.decided_by or "") == "auto-unattended"
    ]
    if auto:
        lines.append(
            f"- **Gate(s) {', '.join(auto)} were accepted automatically** by the unattended "
            "runner, not by a human. Nothing they accepted has been confirmed: treat every "
            "embodiment and range as a candidate until you have checked it."
        )
    rounds = len(state.review.versions)
    open_severe = sum(1 for f in findings if f.severity in {"critical", "high"})
    if open_severe:
        lines.append(
            f"- The review ran {rounds} round(s) and stopped at its budget with {open_severe} "
            "critical or high finding(s) still open. They are all listed above; none has been "
            "answered in the text."
        )
    partial = int(state.counters.get("outputs_partial", 0) or 0)
    if partial:
        lines.append(
            f"- {partial} step(s) produced a partial output the runner kept rather than a "
            "clean one; those sections may be thinner than the rest."
        )
    return lines or ["- Nothing further to flag."]


def findings_report_markdown(dossier: Dossier, state: MatterState, findings: list[Finding]) -> str:
    """The report, as markdown."""
    title = state.matter.title or dossier.slug
    reviewers = sorted({reviewer for f in findings for reviewer in f.reviewers})
    consensus = sum(1 for f in findings if f.consensus)
    by_severity: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_severity[finding.severity].append(finding)

    lines: list[str] = [
        f"# {title} — independent review findings",
        "",
        f"**Matter** `{dossier.slug}` · **Review round** {len(state.review.versions)} · "
        f"**Generated** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"**Reviewers** {', '.join(reviewers) or 'the independent reviewer'}. Each read the "
        "finished description, figures and claims only — never the drafting rationale — and "
        "worked independently of the other.",
        "",
        "---",
        "",
        "## The numbers",
        "",
        *_counts_table(findings),
        "",
        f"{len(findings)} distinct findings. Restatements of one objection across review "
        "steps are merged into a single entry that lists every step that raised it, so this "
        f"count is problems, not paragraphs. {consensus} were raised independently by both "
        "reviewers.",
        "",
        "| Severity | What it means |",
        "|---|---|",
        *[f"| {severity} | {_SEVERITY_MEANS[severity]} |" for severity in _SEVERITY_ORDER],
        "",
        "---",
        "",
    ]

    for severity in ("critical", "high"):
        items = sorted(by_severity.get(severity, []), key=lambda f: (f.category, f.id))
        heading = "Critical findings" if severity == "critical" else "High findings"
        lines += [f"## {heading} ({len(items)})", ""]
        if not items:
            lines += [f"None. No finding was rated {severity}.", "", "---", ""]
            continue
        lines += [
            f"Every {severity} finding, in full. Fix these before the application is filed."
            if severity == "critical"
            else "Each of these needs an amendment or a deliberate decision to accept the risk.",
            "",
        ]
        current = ""
        for finding in items:
            if finding.category != current:
                current = finding.category
                lines += [f"### {_CATEGORY_MEANS.get(current, current).capitalize()}", ""]
            lines += _finding_section(finding)
        lines += ["---", ""]

    minor = sorted(
        by_severity.get("medium", []) + by_severity.get("low", []),
        key=lambda f: (_SEVERITY_ORDER.index(f.severity), f.category, f.id),
    )
    lines += [f"## Medium and low findings ({len(minor)})", ""]
    if minor:
        lines += [
            "Listed for completeness. None of these stops a filing on its own.",
            "",
            "| id | severity | area | claims | finding | suggested fix |",
            "|---|---|---|---|---|---|",
        ]
        for finding in minor:
            fix = finding.least_narrowing_action.replace("|", "/").strip()
            lines.append(
                f"| {finding.id} | {finding.severity} | {finding.category} | "
                f"{_fmt_refs(finding.claim_refs)} | {finding.title.replace('|', '/')} | "
                f"{fix[:180]}{'…' if len(fix) > 180 else ''} |"
            )
    else:
        lines.append("None.")
    lines += [
        "",
        "---",
        "",
        "## What this review could not see",
        "",
        *_limits(dossier, state, findings),
    ]
    lines += [
        "",
        "---",
        "",
        "## What to do next",
        "",
        "1. Read the critical findings above and decide, for each, whether to amend the "
        "claims, add disclosure, or accept the risk knowingly.",
        "2. Answer the open inventor questions (`uv run ipfe questions --matter "
        f"{dossier.slug} --top 10`) — the disclosure is what every later phase is built on.",
        "3. Commission a professional prior-art search.",
        "4. Give this report, the package and the evidence pack to a qualified patent "
        "attorney or agent. No part of this system may authorise a filing.",
        "",
    ]
    return "\n".join(lines) + "\n"


def build_findings_report(
    dossier: Dossier, state: MatterState, review_dir: Path, target: Path
) -> dict[str, Any]:
    """Write the report beside the package. Returns a manifest member."""
    findings = load_findings(review_dir)
    text = findings_report_markdown(dossier, state, findings)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    log.info("findings report: %d findings -> %s", len(findings), target)
    return {
        "name": target.name,
        "source": f"{review_dir.name}/findings.json",
        "findings": len(findings),
        "counts": dict(Counter(finding.severity for finding in findings)),
        "chars": len(text),
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
