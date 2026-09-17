"""Which Phase 1 open questions actually have to be answered at Gate 1.

Phase 1 routinely produces thirty open questions and a human has patience for
ten. Left unranked, the list gets skimmed, the scope-critical question is
answered last, and the answer arrives after Phase 4 has already drafted around
it. So this module ranks them, deterministically and with its reasoning shown:

* **what they block** - a question that blocks the specification or the claims
  (P4/P5) is worth twice one that blocks the prior-art or embodiment analysis;
* **whether an assumption depends on it** - a `blocking: true` assumption whose
  text matches the question means the drafting has *already* guessed;
* **whether it sounds like scope** - "essential", "range", "alternative",
  "genus", "sufficient", "required" are the words claim scope turns on.

Nothing here decides anything. The output is a reading order and a template the
human types into; unanswered questions stay unanswered (`gates._unanswered_questions`
is the record of that), and no rank is ever an instruction to an agent.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, Field

from ecosystem.contracts import deliverable_only
from ecosystem.dossier import Dossier, VersionDir, split_frontmatter
from ecosystem.support_check import SCOPE_KEYWORDS

log = logging.getLogger("ipfe")

__all__ = [
    "ANSWERS_TEMPLATE_NAME",
    "RankedQuestion",
    "top_questions",
    "write_answers_template",
]

ANSWERS_TEMPLATE_NAME: Final[str] = "gate-1-answers.template.md"

# What a blocked phase is worth. P4/P5 produce the filing text: a question that
# blocks them blocks the words that will be examined.
_BLOCK_WEIGHT: Final[dict[str, int]] = {"P4": 2, "P5": 2, "P2": 1, "P3": 1}
_BLOCKING_ASSUMPTION_POINTS: Final[int] = 2
# Above this token overlap, an assumption is treated as being about the question.
_ASSUMPTION_MATCH: Final[float] = 0.5

_BULLET_RE: Final[re.Pattern[str]] = re.compile(r"^[ \t]*(?:[-*•]|\d+[.)])[ \t]+(.+)$")
_QUESTION_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^\**\s*(Q[-_]?\d+|[A-Z]{1,3}\d+)\b[:.)\]]*\s*", re.IGNORECASE
)
_PHASE_RE: Final[re.Pattern[str]] = re.compile(r"\bP[1-5]\b")
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")
_NOISE_TEXT: Final[str] = (
    "the a an is are of to for in on at by with and or do does what which how "
    "we our this that it be should would can if any"
)
_NOISE: Final[frozenset[str]] = frozenset(_NOISE_TEXT.split())


class RankedQuestion(BaseModel):
    """One Phase 1 open question, with why it is where it is."""

    rank: int
    id: str
    text: str
    blocks: list[str] = Field(default_factory=list)
    why: str = ""
    score: int = 0
    sources: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------


class _Candidate:
    """Mutable accumulator: the same question reaches us from several prompts."""

    __slots__ = ("id", "text", "blocks", "sources", "order")

    def __init__(self, identifier: str, text: str, order: int) -> None:
        self.id = identifier
        self.text = text
        self.blocks: list[str] = []
        self.sources: list[str] = []
        self.order = order

    def merge(self, blocks: list[str], source: str) -> None:
        for block in blocks:
            if block and block not in self.blocks:
                self.blocks.append(block)
        if source and source not in self.sources:
            self.sources.append(source)


def _p1_version(dossier: Dossier) -> VersionDir | None:
    """Latest complete Phase 1 version, falling back to the newest that exists.

    Gate 1 is decided *before* the P1 version is marked complete in some
    resume paths, and a human staring at a pending gate needs the questions
    from the version that produced it.
    """
    latest = dossier.latest("P1")
    if latest is not None:
        return latest
    versions = dossier.versions("P1")
    return versions[-1] if versions else None


def _collect(dossier: Dossier) -> tuple[list[_Candidate], list[dict[str, Any]]]:
    """Every open question and every assumption of the latest Phase 1 version."""
    version_dir = _p1_version(dossier)
    candidates: dict[str, _Candidate] = {}
    assumptions: list[dict[str, Any]] = []
    if version_dir is None:
        return [], []

    for envelope_path in sorted(version_dir.path.glob("*.envelope.json")):
        source = envelope_path.name.split(".")[0]
        try:
            data = json.loads(envelope_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("skipping unreadable envelope %s: %s", envelope_path, exc)
            continue
        if not isinstance(data, dict):
            continue
        for item in data.get("open_questions") or []:
            if not isinstance(item, dict):
                continue
            _add(
                candidates,
                str(item.get("id") or "?"),
                str(item.get("text") or ""),
                [str(block) for block in (item.get("blocks") or []) if block],
                source,
            )
        for item in data.get("assumptions") or []:
            if isinstance(item, dict):
                assumptions.append(item)

    _add_consolidated(dossier, candidates)
    return sorted(candidates.values(), key=lambda c: c.order), assumptions


def _add(
    candidates: dict[str, _Candidate],
    identifier: str,
    text: str,
    blocks: list[str],
    source: str,
) -> None:
    body = " ".join((text or "").split())
    if not body:
        return
    key = _key(body)
    candidate = candidates.get(key)
    if candidate is None:
        candidate = _Candidate(identifier, body, len(candidates))
        candidates[key] = candidate
    candidate.merge(blocks, source)


def _add_consolidated(dossier: Dossier, candidates: dict[str, _Candidate]) -> None:
    """The Prompt 05 deliverable usually restates the list for the human.

    It is parsed as bullets, and any `P2`..`P5` mentioned in the bullet counts
    as a blocked phase - that is how the consolidated list expresses it in
    prose. A question only seen here still lands in the ranking; it just has no
    envelope blocks to weigh.
    """
    path = dossier.latest_output("P1", "05", include_running=True)
    if path is None:
        return
    try:
        _, body = split_frontmatter(path.read_text(encoding="utf-8"))
    except OSError as exc:
        log.warning("cannot read %s: %s", path, exc)
        return
    for line in (deliverable_only(body) or body).splitlines():
        match = _BULLET_RE.match(line)
        if not match:
            continue
        text = match.group(1).strip()
        identifier = "?"
        id_match = _QUESTION_ID_RE.match(text)
        if id_match:
            identifier = id_match.group(1).upper().replace("_", "-")
            text = text[id_match.end() :].strip()
        text = text.strip("*_ ").strip()
        # A bullet counts as a question only if it is labelled like one or reads
        # like one. The consolidated deliverable is prose with bullets in it,
        # and hoovering up every bullet would rank "not a question" at all.
        if len(text.split()) < 3 or (identifier == "?" and "?" not in text):
            continue
        _add(candidates, identifier, text, sorted(set(_PHASE_RE.findall(text))), "05-deliverable")


def _key(text: str) -> str:
    """Dedupe key: content words only, so punctuation and case cannot split a question."""
    tokens = [token for token in _WORD_RE.findall(text.lower()) if token not in _NOISE]
    return " ".join(tokens) or text.lower().strip()


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    return {token for token in _WORD_RE.findall((text or "").lower()) if token not in _NOISE}


def _blocking_assumption(candidate: _Candidate, assumptions: list[dict[str, Any]]) -> str | None:
    """The id of a blocking assumption that is about this question, if any."""
    wanted = _tokens(candidate.text)
    if not wanted:
        return None
    for assumption in assumptions:
        if not assumption.get("blocking"):
            continue
        other = _tokens(str(assumption.get("text") or ""))
        if not other:
            continue
        overlap = len(wanted & other) / min(len(wanted), len(other))
        if overlap >= _ASSUMPTION_MATCH:
            return str(assumption.get("id") or "assumption")
    return None


def _score(candidate: _Candidate, assumptions: list[dict[str, Any]]) -> tuple[int, str]:
    reasons: list[str] = []
    score = 0

    weighted = [(block, _BLOCK_WEIGHT.get(block, 0)) for block in candidate.blocks]
    block_points = sum(points for _, points in weighted)
    if block_points:
        score += block_points
        named = ", ".join(block for block, points in weighted if points)
        reasons.append(f"blocks {named} (+{block_points})")

    assumption_id = _blocking_assumption(candidate, assumptions)
    if assumption_id:
        score += _BLOCKING_ASSUMPTION_POINTS
        reasons.append(
            f"blocking assumption {assumption_id} already depends on it "
            f"(+{_BLOCKING_ASSUMPTION_POINTS})"
        )

    lowered = candidate.text.lower()
    hits = [keyword for keyword in SCOPE_KEYWORDS if keyword in lowered]
    if hits:
        score += len(hits)
        reasons.append(f"claim-scope wording: {', '.join(hits)} (+{len(hits)})")

    if len(candidate.sources) > 1:
        reasons.append(f"raised by {', '.join(candidate.sources)}")
    if not reasons:
        reasons.append("no blocked phase, no dependent assumption, no scope wording")
    return score, "; ".join(reasons)


def top_questions(dossier: Dossier, n: int = 10) -> list[RankedQuestion]:
    """The `n` Phase 1 open questions most worth a human's Gate 1 attention.

    Deterministic: equal scores keep the order the prompts raised them in, so
    the same dossier always produces the same list.
    """
    candidates, assumptions = _collect(dossier)
    scored: list[tuple[int, _Candidate, str]] = []
    for candidate in candidates:
        score, why = _score(candidate, assumptions)
        scored.append((score, candidate, why))
    scored.sort(key=lambda row: (-row[0], row[1].order))

    limit = max(0, n)
    return [
        RankedQuestion(
            rank=index,
            id=candidate.id,
            text=candidate.text,
            blocks=list(candidate.blocks),
            why=why,
            score=score,
            sources=list(candidate.sources),
        )
        for index, (score, candidate, why) in enumerate(scored[:limit], start=1)
    ]


# ---------------------------------------------------------------------------
# answers template
# ---------------------------------------------------------------------------


def write_answers_template(dossier: Dossier, questions: list[RankedQuestion]) -> Path:
    """Write `00-intake/gate-1-answers.template.md` for the human to fill in.

    A *template*, not the answers file: `record-decision --answers-file` copies
    whatever the human hands it to `gate-1-answers.md`, and overwriting that
    silently would destroy answers already given.
    """
    target = dossier.intake_dir / ANSWERS_TEMPLATE_NAME
    lines = [
        f"# Gate 1 answers - {dossier.slug}",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d')} from the latest Phase 1 "
        "outputs, ranked by what they block. Write your answer under each question "
        "and delete the ones you cannot answer - an unanswered question stays open, "
        "and the drafting will record it as an assumption rather than invent an answer.",
        "",
        "When you are done:",
        "",
        "```",
        f"uv run ipfe record-decision --matter {dossier.slug} --gate 1 --decision accept \\",
        f"  --by <your-name> --answers-file {target}",
        "```",
        "",
    ]
    if not questions:
        lines += ["*Phase 1 recorded no open questions.*", ""]
    for question in questions:
        blocks = f" (blocks {', '.join(question.blocks)})" if question.blocks else ""
        lines += [
            f"## {question.rank}. {question.id}{blocks}",
            "",
            question.text,
            "",
            f"*Why it is ranked here: {question.why}.*",
            "",
            "**Answer:**",
            "",
            "> ",
            "",
        ]
    return dossier.write_file(target, "\n".join(lines).rstrip() + "\n", overwrite=True)
