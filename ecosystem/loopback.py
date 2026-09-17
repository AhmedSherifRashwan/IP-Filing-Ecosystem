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

Two independent reviewers on different model families run the same prompts
against the same blind package. `aggregate()` merges them into one finding list
and marks the objections both of them found (`consensus`), because a finding two
uncoordinated models reached separately is evidence of a different kind from one
model's opinion - and `plan_routes()` spends the loop-back budget accordingly.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from ecosystem.config import FINDING_CATEGORIES, REVIEW_AGENTS, SECOND_REVIEWER, Settings
from ecosystem.contracts import Correction, Finding, Severity
from ecosystem.state import MatterState

log = logging.getLogger("ipfe")

__all__ = [
    "ROUTES",
    "TITLE_MATCH_RATIO",
    "RoutePlan",
    "aggregate",
    "consensus_summary",
    "corrections_in",
    "counts",
    "is_routable",
    "load_findings",
    "open_categories",
    "plan_routes",
    "residual_findings",
    "reviewer_of",
    "write_consensus_summary",
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
# the corrections it no longer reports have been applied. It supersedes the
# second reviewer's copy (`37b`) too - that audit was of text that no longer
# exists, and re-merging it would resurrect corrections already applied.
_RECHECK_SUFFIX: Final[str] = "r"
# The second reviewer's outputs are written as `<id>b` (`32b`, `40ab`).
_SECOND_SUFFIX: Final[str] = "b"

# difflib ratio above which two titles are taken to name the same objection.
# Low enough that "Claim 1 reads on the admitted prior art" and "Claim 1 is
# anticipated by the admitted prior art" agree; high enough that two different
# claim-1 novelty objections do not. It is only ever a tie-breaker: the category
# and a shared claim or paragraph reference must already match.
TITLE_MATCH_RATIO: Final[float] = 0.6

_SEVERITY_ORDER: Final[dict[str, int]] = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_FRONTMATTER_AGENT: Final[re.Pattern[str]] = re.compile(r"^agent:\s*(\S+)\s*$", re.MULTILINE)
_NORMALISE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def aggregate(review_dir: Path | str, write: bool = True) -> list[Finding]:
    """Merge every review envelope in one review version into deduped findings.

    Within one reviewer the dedupe key is `(category, sorted claim_refs,
    lowercased title)`: the same objection phrased by two of its own prompts is
    one objection. The surviving copy keeps the earliest source prompt.

    Across the two reviewers the match is deliberately fuzzier - see
    `_same_objection` - because two different models will not phrase the same
    objection identically, and refusing to see that they agree would throw away
    the only thing the second reviewer is for. A matched pair collapses into the
    first reviewer's finding, marked `consensus`, carrying the *higher* of the
    two severities; unmatched findings carry the single reviewer that raised
    them. Ids are reassigned `F-<prompt>-<n>` last, so numbering in
    `findings.json` is contiguous per source prompt.
    """
    directory = Path(review_dir)
    by_reviewer = _findings_by_reviewer(directory)
    findings = _renumber(_merge_reviewers(by_reviewer))
    if write:
        write_findings(directory, findings)
        write_consensus_summary(directory, findings, sorted(by_reviewer))
    return findings


def _findings_by_reviewer(directory: Path) -> dict[str, list[Finding]]:
    """Validated findings per reviewer, deduped within each reviewer."""
    seen: dict[str, dict[tuple[str, tuple[str, ...], str], Finding]] = {}
    for path in _envelope_paths(directory):
        data = _read_json(path)
        if data is None:
            continue
        reviewer = reviewer_of(path)
        bucket = seen.setdefault(reviewer, {})
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
            finding.reviewers = [reviewer]
            finding.consensus = False
            key = (
                finding.category,
                tuple(sorted(str(ref) for ref in finding.claim_refs)),
                finding.title.strip().lower(),
            )
            bucket.setdefault(key, finding)
    return {reviewer: list(bucket.values()) for reviewer, bucket in seen.items()}


def _merge_reviewers(by_reviewer: dict[str, list[Finding]]) -> list[Finding]:
    """Collapse matched A/B pairs into one consensus finding, greedily and once."""
    primary = REVIEW_AGENTS[0]
    first = list(by_reviewer.get(primary, []))
    # Anything from an id we do not recognise is treated as the second opinion
    # rather than dropped: an unmerged finding is recoverable, a lost one is not.
    second = [f for reviewer, items in by_reviewer.items() if reviewer != primary for f in items]
    if not second:
        return first

    taken: set[int] = set()
    for finding in first:
        best: int | None = None
        best_ratio = 0.0
        for index, candidate in enumerate(second):
            if index in taken or not _same_objection(finding, candidate):
                continue
            ratio = _title_ratio(finding.title, candidate.title)
            if ratio > best_ratio:
                best, best_ratio = index, ratio
        if best is None:
            continue
        taken.add(best)
        partner = second[best]
        finding.reviewers = [*finding.reviewers, *partner.reviewers]
        finding.consensus = True
        # The more alarming reading wins. Two models disagreeing about how bad
        # something is are still agreeing that it is there.
        finding.severity = _worse(finding.severity, partner.severity)
    return first + [f for index, f in enumerate(second) if index not in taken]


def _same_objection(left: Finding, right: Finding) -> bool:
    """Same category, an overlapping reference, and a recognisably similar title."""
    if left.category != right.category:
        return False
    if not (_refs(left.claim_refs) & _refs(right.claim_refs)) and not (
        _refs(left.spec_refs) & _refs(right.spec_refs)
    ):
        return False
    return _title_ratio(left.title, right.title) >= TITLE_MATCH_RATIO


def _refs(refs: Iterable[Any]) -> set[str]:
    """Reference strings, case- and punctuation-normalised (`Claim 1` == `claim 1`)."""
    return {normalised for ref in refs if (normalised := _normalise(str(ref)))}


def _title_ratio(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, _normalise(left), _normalise(right)).ratio()


def _normalise(text: str) -> str:
    return _NORMALISE.sub(" ", (text or "").lower()).strip()


def _worse(left: Severity, right: Severity) -> Severity:
    return left if _severity_rank(left) <= _severity_rank(right) else right


def reviewer_of(path: Path) -> str:
    """Which reviewer wrote this envelope.

    The output's own frontmatter (`agent: ip-reviewer-b`) is the record, because
    it is what the runner actually sent the call as. The `<id>b` naming is the
    fallback, for an envelope that has no markdown sibling.
    """
    stem = _stem(path)
    for sibling in sorted(path.parent.glob(f"{stem}-*.md")):
        match = _FRONTMATTER_AGENT.search(sibling.read_text(encoding="utf-8")[:2000])
        if match and match.group(1) in REVIEW_AGENTS:
            return match.group(1)
    return SECOND_REVIEWER if stem.endswith(_SECOND_SUFFIX) else REVIEW_AGENTS[0]


def consensus_summary(findings: Iterable[Finding]) -> dict[str, Any]:
    """How the two reviewers landed, overall and per severity.

    `a_only`/`b_only` are not a scoreboard. They are the number of findings that
    rest on one model's judgement alone, which is exactly the population counsel
    should read with more suspicion than the consensus set.
    """
    empty = {"consensus": 0, "a_only": 0, "b_only": 0}
    totals = dict(empty)
    by_severity = {severity: dict(empty) for severity in _SEVERITY_ORDER}
    for finding in findings:
        bucket = _bucket(finding)
        totals[bucket] += 1
        by_severity.setdefault(finding.severity, dict(empty))[bucket] += 1
    return {"counts": totals, "by_severity": by_severity}


def _bucket(finding: Finding) -> str:
    if finding.consensus or len(finding.reviewers) > 1:
        return "consensus"
    return "b_only" if finding.reviewers == [SECOND_REVIEWER] else "a_only"


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


def write_consensus_summary(
    review_dir: Path | str, findings: list[Finding], reviewers: list[str] | None = None
) -> Path:
    directory = Path(review_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "consensus-summary.json"
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "reviewers": reviewers if reviewers is not None else sorted(_reviewers_in(findings)),
        **consensus_summary(findings),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _reviewers_in(findings: Iterable[Finding]) -> set[str]:
    return {reviewer for finding in findings for reviewer in finding.reviewers}


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


def residual_findings(
    findings: Iterable[Finding], settings: Settings | None = None
) -> list[Finding]:
    """Everything that is reported to the human rather than routed back.

    With `settings`, this includes the single-reviewer high findings that
    `plan_routes` declines to route: they are leads, and a lead that is never
    written down is a lead that was suppressed.
    """
    return [
        finding
        for finding in findings
        if finding.severity not in SEVERE
        or (settings is not None and not is_routable(finding, settings))
    ]


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
    # High findings only one of the two reviewers raised. Not routed, not
    # forgotten: they go to the human on the residual list, tagged.
    single_reviewer_highs: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Nothing to route, nothing capped, nothing formal outstanding."""
        return not self.steps and not self.capped and not self.formal


def is_routable(finding: Finding, settings: Settings) -> bool:
    """Whether one finding is worth spending a loop-back cycle on.

    A single hostile model's "high" is a lead. Two independent models, on
    different families, blind to each other and to the drafting rationale,
    reaching the same "high" separately is evidence. The difference matters
    because a loop-back cycle is expensive and strictly capped: each one re-runs
    Phases 3-5 and burns one of the two cycles that category will ever get, so
    routing on a lead can leave nothing in the budget for the finding that turns
    out to be real.

    So:

    * **Critical always routes**, consensus or not. If either reviewer believes
      the claim falls as drafted, the cost of being wrong about that dwarfs the
      cost of a wasted cycle. We do not make a model out-vote a claim-killer.
    * **High routes on agreement** - or when there is no second reviewer at all
      (`IPFE_SECOND_REVIEWER=0`), where the old single-reviewer rule is the only
      rule available and a lead is all the evidence there is.
    * **A single-reviewer high is carried, not dropped.** It goes to the human
      on the residual list tagged `single-reviewer`, so counsel weighs it with
      the knowledge that the other reviewer did not see it.

    Medium and low never route, exactly as before.
    """
    if finding.severity == "critical":
        return True
    if finding.severity != "high":
        return False
    if not settings.second_reviewer:
        return True
    return finding.consensus


def plan_routes(
    findings: list[Finding],
    state: MatterState,
    settings: Settings,
) -> RoutePlan:
    """Decide the combined loop-back pass for one review round.

    Categories are considered independently for budget but routed together: the
    drafters see every routed finding in one revision rather than three, which
    is both cheaper and less likely to produce contradictory edits. What is
    eligible to be routed at all is `is_routable`.
    """
    plan = RoutePlan()
    severe = [finding for finding in findings if is_routable(finding, settings)]
    plan.single_reviewer_highs = [
        finding.id
        for finding in findings
        if finding.severity in SEVERE and not is_routable(finding, settings)
    ]
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
    return any(finding.suggested_target == "P5" or finding.claim_refs for finding in findings)


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
    superseded: set[str] = set()
    for stem in stems:
        if not stem.endswith(_RECHECK_SUFFIX):
            continue
        base = stem[: -len(_RECHECK_SUFFIX)]
        # Both reviewers audited the pre-fix text; the recheck replaces both.
        superseded |= {base, base + _SECOND_SUFFIX} & stems
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
    return _SEVERITY_ORDER.get(severity, len(_SEVERITY_ORDER))
