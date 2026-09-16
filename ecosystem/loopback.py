"""Phase-7 loop-back engine: merge the reviewer's findings, decide where they go.

The reviewer produces twelve envelopes per review round and they overlap heavily
- the same weakness is found by the examiner red-team, the litigation red-team
and the gap analysis. `aggregate()` merges them into one `findings.json` and
dedupes, so a single weakness routes once and counts once against the cap.

Two rules keep this honest:

* **Only critical and high route.** Medium and low are real, but routing them
  would loop forever; they go to `residual-findings.md` for the human.
* **Formal findings never consume a category's cap.** A formal fix does not
  narrow scope, so spending a novelty loop-back budget on a missing antecedent
  basis would be absurd. They have their own counter and their own cap.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from ecosystem.config import FINDING_CATEGORIES, Settings
from ecosystem.contracts import Correction, Finding
from ecosystem.state import MatterState

log = logging.getLogger("ipfe")

__all__ = [
    "ROUTES",
    "RoutePlan",
    "aggregate",
    "corrections_in",
    "counts",
    "load_findings",
    "open_categories",
    "plan_routes",
    "residual_findings",
    "write_findings",
]

SEVERE: Final[frozenset[str]] = frozenset({"critical", "high"})

# Where a category's critical/high findings are sent, in pipeline order, and
# whether the round counts against `loopback.cap_per_category` (plan s3.9).
ROUTES: Final[dict[str, dict[str, Any]]] = {
    "novelty": {
        "steps": ["x-loopback-p3", "x-p4-revise", "x-p5-revise"],
        "counts_against_cap": True,
        "gate2_reconfirm": True,
    },
    "design-around": {
        "steps": ["x-loopback-p3", "x-p4-revise", "x-p5-revise"],
        "counts_against_cap": True,
        "gate2_reconfirm": True,
    },
    "enablement": {
        # P5 only when a cited support passage moved; see `_enablement_touches_claims`.
        "steps": ["x-p4-revise", "x-p5-revise"],
        "counts_against_cap": True,
        "gate2_reconfirm": False,
    },
    "support": {
        # "Add disclosure, do not narrow." Any narrowing the reviewer suggested
        # is surfaced to the human instead of being applied.
        "steps": ["x-p4-revise"],
        "counts_against_cap": True,
        "gate2_reconfirm": False,
    },
    "formal": {
        "steps": ["x-formal-fix"],
        "counts_against_cap": False,
        "gate2_reconfirm": False,
    },
}

# The one canonical ordering of a combined pass: embodiments, then description,
# then claims. Running P5 before P4 would claim support that does not exist yet.
STEP_ORDER: Final[tuple[str, ...]] = ("x-loopback-p3", "x-p4-revise", "x-p5-revise")

# A recheck envelope (`37r`) supersedes the original (`37`) after a formal fix:
# the corrections it no longer reports have been applied.
_RECHECK_SUFFIX: Final[str] = "r"


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def aggregate(review_dir: Path | str, write: bool = True) -> list[Finding]:
    """Merge every review envelope in one review version into deduped findings.

    Dedupe key is `(category, sorted claim_refs, lowercased title)`: the same
    objection phrased by two prompts is one objection. The surviving copy keeps
    the earliest source prompt, and ids are reassigned `F-<prompt>-<n>` so the
    numbering in `findings.json` is contiguous per source.
    """
    directory = Path(review_dir)
    envelopes = _envelope_paths(directory)

    seen: dict[tuple[str, tuple[str, ...], str], Finding] = {}
    for path in envelopes:
        data = _read_json(path)
        if data is None:
            continue
        for raw in data.get("findings") or []:
            if not isinstance(raw, dict):
                continue
            raw.setdefault("source_prompt", _stem(path))
            try:
                finding = Finding.model_validate(raw)
            except ValidationError as exc:
                # A malformed finding is evidence of a contract miss that got
                # through; log it loudly rather than dropping it silently.
                log.warning("skipping malformed finding in %s: %s", path.name, exc)
                continue
            key = (
                finding.category,
                tuple(sorted(str(ref) for ref in finding.claim_refs)),
                finding.title.strip().lower(),
            )
            if key not in seen:
                seen[key] = finding
    findings = _renumber(list(seen.values()))
    if write:
        write_findings(directory, findings)
    return findings


def write_findings(review_dir: Path | str, findings: list[Finding]) -> Path:
    directory = Path(review_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "findings.json"
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "counts": counts(findings),
        "open_categories": open_categories(findings),
        "findings": [finding.model_dump(mode="json", by_alias=True) for finding in findings],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_findings(review_dir: Path | str) -> list[Finding]:
    path = Path(review_dir) / "findings.json"
    data = _read_json(path)
    if data is None:
        return []
    out: list[Finding] = []
    for raw in data.get("findings") or []:
        try:
            out.append(Finding.model_validate(raw))
        except ValidationError as exc:
            log.warning("skipping malformed finding in %s: %s", path.name, exc)
    return out


def corrections_in(review_dir: Path | str) -> list[Correction]:
    """Every mechanical correction the formal passes reported, deduped on target+location."""
    out: dict[tuple[str, str, str], Correction] = {}
    for path in _envelope_paths(Path(review_dir)):
        data = _read_json(path)
        if data is None:
            continue
        for raw in data.get("corrections") or []:
            if not isinstance(raw, dict):
                continue
            try:
                correction = Correction.model_validate(raw)
            except ValidationError as exc:
                log.warning("skipping malformed correction in %s: %s", path.name, exc)
                continue
            out.setdefault(
                (correction.target, correction.location, correction.from_text), correction
            )
    return list(out.values())


def counts(findings: Iterable[Finding]) -> dict[str, int]:
    tally = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        tally[finding.severity] = tally.get(finding.severity, 0) + 1
    return tally


def open_categories(findings: Iterable[Finding]) -> list[str]:
    """Categories with at least one critical or high finding, in pipeline order."""
    found = {f.category for f in findings if f.severity in SEVERE}
    return [category for category in FINDING_CATEGORIES if category in found]


def residual_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Everything that is reported to the human rather than routed back."""
    return [finding for finding in findings if finding.severity not in SEVERE]


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------


@dataclass
class RoutePlan:
    """What the runner does with one review round's findings."""

    # Categories being routed this cycle, in pipeline order.
    categories: list[str] = field(default_factory=list)
    # The combined pass, ordered P3 -> P4 -> P5.
    steps: list[str] = field(default_factory=list)
    # Categories that are out of budget: these become a loop-back cap gate.
    capped: list[str] = field(default_factory=list)
    # Formal corrections run on their own counter and never enter `steps`.
    formal: bool = False
    formal_exhausted: bool = False
    gate2_reconfirm: bool = False
    finding_ids: dict[str, list[str]] = field(default_factory=dict)
    narrowing_notes: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Nothing to route, nothing capped, nothing formal outstanding."""
        return not self.steps and not self.capped and not self.formal


def plan_routes(
    findings: list[Finding],
    state: MatterState,
    settings: Settings,
) -> RoutePlan:
    """Decide the combined loop-back pass for one review round.

    Categories are considered independently for budget but routed together: the
    drafters see every routed finding in one revision rather than three, which
    is both cheaper and less likely to produce contradictory edits.
    """
    plan = RoutePlan()
    severe = [finding for finding in findings if finding.severity in SEVERE]
    by_category: dict[str, list[Finding]] = {}
    for finding in severe:
        by_category.setdefault(finding.category, []).append(finding)

    if "formal" in by_category or _has_formal_corrections(state):
        plan.formal = True
        plan.formal_exhausted = state.loopback.formal_fix_passes >= settings.formal_fix_cap
        if plan.formal_exhausted:
            plan.formal = False
        plan.finding_ids["formal"] = [f.id for f in by_category.get("formal", [])]

    steps: set[str] = set()
    for category in FINDING_CATEGORIES:
        if category == "formal" or category not in by_category:
            continue
        route = ROUTES[category]
        cycles = state.loopback.cycles.get(category, 0)
        if cycles >= allowance(state, settings, category):
            plan.capped.append(category)
            plan.finding_ids[category] = [f.id for f in by_category[category]]
            continue
        plan.categories.append(category)
        plan.finding_ids[category] = [f.id for f in by_category[category]]
        for step in route["steps"]:
            # "only if a cited support passage changed" - an enablement fix adds
            # disclosure; the claims are only re-run when the reviewer aimed the
            # finding at them.
            if (
                step == "x-p5-revise"
                and category == "enablement"
                and not _enablement_touches_claims(by_category[category])
            ):
                continue
            steps.add(step)
        if route["gate2_reconfirm"] and settings.gate2_reconfirm_on_loopback:
            plan.gate2_reconfirm = True

    plan.steps = [step for step in STEP_ORDER if step in steps]
    plan.narrowing_notes = [
        f"{finding.id} ({finding.category}): {finding.least_narrowing_action}"
        for finding in by_category.get("support", [])
        if _suggests_narrowing(finding)
    ]
    return plan


def allowance(state: MatterState, settings: Settings, category: str) -> int:
    """Cap for a category, plus any extra cycle a human forced at a cap gate.

    `manual-fix` and `narrow` both buy exactly one more re-review without
    resetting the counter, so a matter cannot be looped indefinitely by
    re-deciding the same gate.
    """
    extra = sum(
        1
        for decision in state.loopback.forced_decisions
        if decision.category == category and decision.decision in {"manual-fix", "narrow"}
    )
    return settings.loopback_cap + extra


def _has_formal_corrections(state: MatterState) -> bool:
    """Formal work can be outstanding without a formal *finding*: 37/38/40a may
    report only `corrections[]`. The runner sets this flag on the state before
    asking for a plan."""
    return bool(state.counters.get("pending_formal_corrections", 0))


def _enablement_touches_claims(findings: list[Finding]) -> bool:
    return any(
        finding.suggested_target == "P5" or finding.claim_refs for finding in findings
    )


def _suggests_narrowing(finding: Finding) -> bool:
    text = f"{finding.least_narrowing_action} {finding.attack}".lower()
    return "narrow" in text or "limit the claim" in text


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _envelope_paths(directory: Path) -> list[Path]:
    """Envelopes of one review version, with superseded originals removed.

    After a formal fix the runner re-runs 37/38/40a and stores them as `37r`.
    Merging both copies would resurrect corrections that have just been applied,
    so the recheck wins outright.
    """
    if not directory.is_dir():
        return []
    paths = sorted(directory.glob("*.envelope.json"))
    stems = {_stem(path) for path in paths}
    superseded = {
        stem[: -len(_RECHECK_SUFFIX)]
        for stem in stems
        if stem.endswith(_RECHECK_SUFFIX) and stem[: -len(_RECHECK_SUFFIX)] in stems
    }
    return [path for path in paths if _stem(path) not in superseded]


def _stem(path: Path) -> str:
    return path.name.split(".")[0]


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("skipping unreadable %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _renumber(findings: list[Finding]) -> list[Finding]:
    """`F-<prompt>-<n>`, numbered from 1 within each source prompt."""
    findings.sort(key=lambda f: (f.source_prompt, _severity_rank(f.severity), f.title.lower()))
    per_source: dict[str, int] = {}
    for finding in findings:
        per_source[finding.source_prompt] = per_source.get(finding.source_prompt, 0) + 1
        finding.id = f"F-{finding.source_prompt}-{per_source[finding.source_prompt]}"
    return findings


def _severity_rank(severity: str) -> int:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return order.get(severity, 4)
