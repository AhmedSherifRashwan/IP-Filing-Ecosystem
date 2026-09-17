"""Deterministic claim-support checker: does the description say what the claims claim?

Written-description support is the cheapest defect to find and the most expensive
to discover at examination, so this module answers one narrow question in pure
Python: for every limitation of every claim, is there a paragraph of the
description that says it?

Three anchoring methods, tried in order and reported by name, so a reader can
see *why* a limitation was treated as supported:

1. **exact** - the limitation's words appear verbatim in a paragraph.
2. **normalised** - they appear after lowercasing, dropping reference numerals,
   singularising and removing function words.
3. **overlap** - at least `OVERLAP_THRESHOLD` of the limitation's content words
   appear in one paragraph.

This is a *heuristic*. It complements and never replaces the independent
reviewer's Prompt 28 support matrix: it cannot tell whether a paragraph supports
a limitation in the legal sense, only whether the words are there. A `likely`
means "a human should read the cited paragraph", and an `ok: true` means
"nothing is obviously missing", never "the claims are supported".
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

__all__ = [
    "Anchor",
    "ClaimSummary",
    "DependencyCheck",
    "DependencyIssue",
    "Limitation",
    "OVERLAP_THRESHOLD",
    "ParsedClaim",
    "Paragraph",
    "SupportReport",
    "check_support",
    "parse_claims",
    "parse_paragraphs",
    "write_support_check",
]

AnchorMethod = Literal["exact", "normalised", "overlap"]
Status = Literal["explicit", "likely", "unsupported"]

# A limitation whose content words are this well covered by one paragraph is
# reported as `likely`. Below it, nothing is cited at all: a weak citation is
# worse than none, because a reader trusts it.
OVERLAP_THRESHOLD: Final[float] = 0.6

# `1. A valve core comprising:` at the start of a line. Three digits is already
# a pathological claim set; four would be a page number.
_CLAIM_START_RE: Final[re.Pattern[str]] = re.compile(r"^[ \t]{0,3}(\d{1,3})[.)][ \t]+(?=\S)", re.M)

# `[0031]` (US/EP publication style) or `¶31`.
_PARA_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[ \t]*(?:\[(\d{3,5})\]|¶[ \t]*(\d{1,5}))[ \t]*")

# Where a claim breaks into limitations. Order matters: the longest alternative
# must be tried first or `, wherein` is eaten by `,\s*and`.
_SPLIT_RE: Final[re.Pattern[str]] = re.compile(
    r"""
      ;[ \t]*(?:and[ \t]+)?      # semicolon list, with or without the trailing and
    | \bcomprising:[ \t]*        # `... comprising:` opens a list
    | ,?[ \t]*\bfurther[ \t]+comprising[ \t]*:?[ \t]*
    | \bconsisting[ \t]+of:[ \t]*
    | \bincluding:[ \t]*
    | ,[ \t]*wherein[ \t]+
    | ,[ \t]*whereby[ \t]+
    | ,[ \t]*and[ \t]+
    | \bwherein[ \t]+
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A leading bullet or sub-paragraph label, stripped before a chunk is measured.
_BULLET_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t]*(?:[-*•‣]|\(?[a-z]\)|\(?[ivx]+\))[ \t]+", re.IGNORECASE
)

# `of claim 3`, `according to claim 3`, `as claimed in claim 3`.
_DEPENDENCY_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:of|according[ \t]+to|as[ \t]+claimed[ \t]+in|as[ \t]+in|in)[ \t]+claims?[ \t]+(\d{1,3})",
    re.IGNORECASE,
)
# A chunk that is nothing but a dependency preamble carries no limitation.
_PREAMBLE_ONLY_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:the|a|an)?[ \t]*[\w \t,-]{0,80}?\b(?:of|according[ \t]+to|as[ \t]+claimed[ \t]+in)"
    r"[ \t]+claims?[ \t]+\d{1,3}[ \t]*$",
    re.IGNORECASE,
)

# Reference numerals (`seat ring 30`) and measurements carry no semantic weight
# for matching: the description may introduce the same part with a different
# numeral in a later embodiment.
_NUMERAL_RE: Final[re.Pattern[str]] = re.compile(r"\b\d+[a-z]?\b", re.IGNORECASE)
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z-]*")

# Function words carry no support signal; a word list stays a word list so it
# remains editable by a human reading the checker's output.
_STOP_WORD_TEXT: Final[str] = """
    a an the of to for in on at by with and or said such that which who whose
    is are be being been as into from onto upon between among over under above
    below about around through during while when where why how each any all both
    least most more than other another same different further comprising comprises
    comprise consisting consists including includes include having have has had
    wherein whereby thereby therein thereof thereto whereof so arranged
    configured adapted operable capable means step steps method apparatus system
    device claim claims one two three first second third fourth respective
    respectively substantially generally approximately it its their
    there this these those may can will would could should
"""
_STOP_WORDS: Final[frozenset[str]] = frozenset(_STOP_WORD_TEXT.split())

# Words that signal claim scope rather than mechanism. Defined here, and used by
# `questions.py`, so both modules rank the same vocabulary as scope-relevant.
SCOPE_KEYWORDS: Final[tuple[str, ...]] = (
    "essential",
    "range",
    "alternative",
    "genus",
    "sufficient",
    "required",
)


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


class Paragraph(BaseModel):
    """One description paragraph and the id a reader can cite."""

    id: str
    index: int
    text: str


class ParsedClaim(BaseModel):
    number: int
    text: str
    depends_on: list[int] = Field(default_factory=list)

    @property
    def independent(self) -> bool:
        return not self.depends_on


class Anchor(BaseModel):
    paragraph_id: str
    score: float
    method: AnchorMethod
    excerpt: str = ""


class Limitation(BaseModel):
    claim: int
    index: int
    text: str
    anchor: Anchor | None = None
    status: Status = "unsupported"


class ClaimSummary(BaseModel):
    claim: int
    independent: bool
    depends_on: list[int] = Field(default_factory=list)
    limitations: int = 0
    explicit: int = 0
    likely: int = 0
    unsupported: int = 0


class DependencyIssue(BaseModel):
    claim: int
    refers_to: int
    problem: str


class DependencyCheck(BaseModel):
    ok: bool = True
    issues: list[DependencyIssue] = Field(default_factory=list)


class SupportReport(BaseModel):
    """What the checker found. `ok` is advisory - see the module docstring."""

    ok: bool = True
    unsupported_count: int = 0
    claims: list[ClaimSummary] = Field(default_factory=list)
    limitations: list[Limitation] = Field(default_factory=list)
    dependency_check: DependencyCheck = Field(default_factory=DependencyCheck)
    paragraphs: int = 0
    method: str = "deterministic heuristic; complements the reviewer's Prompt 28 matrix"

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, data: str | bytes | dict[str, Any]) -> SupportReport:
        if isinstance(data, str | bytes):
            data = json.loads(data)
        return cls.model_validate(data)

    def to_markdown(self, title: str = "Claim support check") -> str:
        lines = [
            f"# {title}",
            "",
            "Deterministic word-level check of every claim limitation against the "
            "description. It is a heuristic that complements - and never replaces - "
            "the independent reviewer's Prompt 28 support matrix. `explicit` means "
            "the words appear verbatim; `likely` means a paragraph covers the same "
            "content words and a human should read it; `unsupported` means nothing "
            "in the description matched.",
            "",
            f"{len(self.claims)} claim(s), {len(self.limitations)} limitation(s), "
            f"{self.unsupported_count} unsupported, {self.paragraphs} description "
            "paragraph(s).",
            "",
            "| claim | limitation | anchor | status |",
            "| --- | --- | --- | --- |",
        ]
        for limitation in self.limitations:
            anchor = limitation.anchor
            cited = f"{anchor.paragraph_id} ({anchor.method} {anchor.score:.2f})" if anchor else "-"
            lines.append(
                f"| {limitation.claim} | {_cell(limitation.text)} | {cited} | {limitation.status} |"
            )

        lines += ["", "## Dependencies", ""]
        if self.dependency_check.ok:
            lines.append("Every dependent claim refers to an earlier claim that exists.")
        else:
            for issue in self.dependency_check.issues:
                lines.append(f"- claim {issue.claim} -> claim {issue.refers_to}: {issue.problem}")

        unsupported_independent = [
            limitation
            for limitation in self.limitations
            if limitation.status == "unsupported" and _is_independent(self.claims, limitation.claim)
        ]
        lines += ["", "## Unsupported limitations in independent claims", ""]
        if not unsupported_independent:
            lines.append("None found by this check.")
        else:
            for limitation in unsupported_independent:
                lines.append(f"- claim {limitation.claim}: {limitation.text}")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def parse_paragraphs(description_md: str) -> list[Paragraph]:
    """Split a description into citable paragraphs.

    `[0031]` and `¶31` markers are kept verbatim because they are what a reader
    cites; an unnumbered description falls back to `¶<n>` by position, which is
    still stable for one version of the text.
    """
    text = (description_md or "").replace("\r\n", "\n")
    paragraphs: list[Paragraph] = []
    for index, block in enumerate(re.split(r"\n[ \t]*\n", text), start=1):
        body = block.strip()
        if not body:
            continue
        match = _PARA_ID_RE.match(body)
        if match:
            identifier = f"[{match.group(1)}]" if match.group(1) else f"¶{match.group(2)}"
            body = body[match.end() :].strip()
        else:
            identifier = f"¶{len(paragraphs) + 1}"
        if body:
            paragraphs.append(Paragraph(id=identifier, index=index, text=body))
    return paragraphs


def parse_claims(claims_md: str) -> list[ParsedClaim]:
    """Parse numbered plain-text claims. Numbering is taken from the text, not counted."""
    text = (claims_md or "").replace("\r\n", "\n")
    starts = list(_CLAIM_START_RE.finditer(text))
    claims: list[ParsedClaim] = []
    for position, match in enumerate(starts):
        end = starts[position + 1].start() if position + 1 < len(starts) else len(text)
        body = text[match.end() : end].strip()
        if not body:
            continue
        number = int(match.group(1))
        claims.append(
            ParsedClaim(number=number, text=body, depends_on=_dependencies(body)),
        )
    return claims


def split_limitations(claim_text: str) -> list[str]:
    """Break one claim into the units a support matrix has a row for.

    A newline is only a boundary when the next line *starts* a bullet or a
    lettered sub-paragraph. Claims arrive hard-wrapped, and treating every line
    break as a limitation boundary would chop "a burr / set 20" in half and then
    report both halves as unsupported.
    """
    out: list[str] = []
    for segment in _segments(claim_text):
        for chunk in _SPLIT_RE.split(segment):
            cleaned = _clean_chunk(chunk)
            if cleaned and not _PREAMBLE_ONLY_RE.match(cleaned):
                out.append(cleaned)
    return out


def _segments(claim_text: str) -> list[str]:
    """Rejoin wrapped lines; start a new segment at a bullet or a blank line."""
    segments: list[str] = []
    current: list[str] = []
    for line in (claim_text or "").split("\n"):
        stripped = line.strip()
        if not stripped:
            if current:
                segments.append(" ".join(current))
                current = []
            continue
        if _BULLET_RE.match(line):
            if current:
                segments.append(" ".join(current))
            current = [_BULLET_RE.sub("", line).strip()]
            continue
        current.append(stripped)
    if current:
        segments.append(" ".join(current))
    return [segment for segment in segments if segment]


def _clean_chunk(chunk: str) -> str:
    cleaned = re.sub(r"\s+", " ", (chunk or "")).strip().strip(",")
    cleaned = re.sub(r"^(?:and|or)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = cleaned.rstrip(".;: ").strip()
    cleaned = _drop_preamble_echo(cleaned)
    # Two words is the shortest thing that can be a limitation ("a housing").
    return cleaned if len(cleaned.split()) >= 2 else ""


def _drop_preamble_echo(chunk: str) -> str:
    """Remove the `, the method` in "A method of purging a valve core, the method".

    Method preambles re-name their subject before `comprising:`, which leaves a
    dangling fragment on the preamble chunk. Only dropped when the noun already
    appears earlier in the same chunk, so a genuine two-element list ("a body,
    the lid") survives.
    """
    match = re.search(r",[ \t]*(?:the|a|an)[ \t]+([\w-]+)$", chunk, re.IGNORECASE)
    if not match:
        return chunk
    noun = match.group(1).lower()
    head = chunk[: match.start()]
    return head if noun in {word.lower().strip(",") for word in head.split()} else chunk


def _dependencies(claim_text: str) -> list[int]:
    found: list[int] = []
    for match in _DEPENDENCY_RE.finditer(claim_text or ""):
        number = int(match.group(1))
        if number not in found:
            found.append(number)
    return found


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------


def _singular(word: str) -> str:
    if len(word) <= 3 or word.endswith("ss"):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def _content_tokens(text: str) -> list[str]:
    """Lowercase, numeral-free, singularised content words, in order."""
    lowered = _NUMERAL_RE.sub(" ", (text or "").lower())
    tokens = [_singular(word) for word in _WORD_RE.findall(lowered)]
    return [token for token in tokens if token and token not in _STOP_WORDS]


def _variants(token: str) -> set[str]:
    """Every spelling of one word this checker treats as the same word.

    Drafting says "radial expansion" and "the seat ring wipes" where a claim
    says "expand radially" and "wiping"; a checker blind to that inflection
    reports support gaps that are not there, and a checker that cries wolf gets
    ignored. Generating variants (rather than stemming to one form) keeps the
    comparison symmetric and can only ever make it more forgiving - it never
    invents a match between different words.
    """
    forms = {token}
    if token.endswith("ied") and len(token) > 4:
        forms.add(token[:-3] + "y")
    if token.endswith("ying") and len(token) > 5:
        forms.add(token[:-4] + "y")
    for suffix, cut in (("ing", 3), ("ed", 2), ("ly", 2)):
        if token.endswith(suffix) and len(token) - cut >= 3:
            base = token[: len(token) - cut]
            forms.add(base)
            forms.add(base + "e")
            if len(base) > 2 and base[-1] == base[-2]:
                forms.add(base[:-1])
    return forms


def _variant_index(text: str) -> set[str]:
    """Every variant of every content word in a text - the haystack side."""
    index: set[str] = set()
    for token in _content_tokens(text):
        index |= _variants(token)
    return index


def _covered(limitation: str, index: set[str]) -> float:
    """Fraction of the limitation's content words that the haystack covers."""
    tokens = list(dict.fromkeys(_content_tokens(limitation)))
    if not tokens:
        return 0.0
    hits = sum(1 for token in tokens if _variants(token) & index)
    return hits / len(tokens)


def _normalised(text: str) -> str:
    return " ".join(_content_tokens(text))


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


# ---------------------------------------------------------------------------
# anchoring
# ---------------------------------------------------------------------------


def _anchor_for(limitation: str, paragraphs: list[Paragraph]) -> Anchor | None:
    """Best anchor for one limitation, by the first method that fires."""
    needle = _flat(limitation).rstrip(".")
    if needle:
        for paragraph in paragraphs:
            if needle in _flat(paragraph.text):
                return Anchor(
                    paragraph_id=paragraph.id,
                    score=1.0,
                    method="exact",
                    excerpt=_excerpt(paragraph.text),
                )

    normalised = _normalised(limitation)
    if normalised:
        for paragraph in paragraphs:
            if normalised in _normalised(paragraph.text):
                return Anchor(
                    paragraph_id=paragraph.id,
                    score=0.9,
                    method="normalised",
                    excerpt=_excerpt(paragraph.text),
                )

    if not _content_tokens(limitation):
        return None
    best: tuple[float, str, str] | None = None
    for identifier, text in _anchor_units(paragraphs):
        score = _covered(limitation, _variant_index(text))
        if best is None or score > best[0]:
            best = (score, identifier, text)
    if best is None or best[0] < OVERLAP_THRESHOLD:
        return None
    return Anchor(
        paragraph_id=best[1],
        score=round(best[0], 3),
        method="overlap",
        excerpt=_excerpt(best[2]),
    )


def _anchor_units(paragraphs: list[Paragraph]) -> list[tuple[str, str]]:
    """Single paragraphs first, then adjacent pairs.

    Support is routinely written across two consecutive paragraphs (the part in
    one, what it does in the next). A pair is cited as `[0002]+[0003]` so the
    reader is told exactly what was matched, and singles are offered first so a
    tie never prefers the vaguer citation.
    """
    units = [(paragraph.id, paragraph.text) for paragraph in paragraphs]
    units += [
        (f"{first.id}+{second.id}", f"{first.text}\n{second.text}")
        for first, second in zip(paragraphs, paragraphs[1:], strict=False)
    ]
    return units


def _excerpt(text: str, limit: int = 160) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def check_support(description_md: str, claims_md: str) -> SupportReport:
    """Anchor every claim limitation to a description paragraph.

    Pure: same inputs, same report. Nothing is written and nothing is fixed -
    the output is evidence for a human, not an instruction to an agent.
    """
    paragraphs = parse_paragraphs(description_md)
    claims = parse_claims(claims_md)
    numbers = {claim.number for claim in claims}

    limitations: list[Limitation] = []
    summaries: list[ClaimSummary] = []

    for claim in claims:
        summary = ClaimSummary(
            claim=claim.number,
            independent=claim.independent,
            depends_on=list(claim.depends_on),
        )
        for index, text in enumerate(split_limitations(claim.text), start=1):
            anchor = _anchor_for(text, paragraphs)
            status: Status = (
                "explicit"
                if anchor is not None and anchor.method == "exact"
                else "likely"
                if anchor is not None
                else "unsupported"
            )
            limitations.append(
                Limitation(claim=claim.number, index=index, text=text, anchor=anchor, status=status)
            )
            summary.limitations += 1
            if status == "explicit":
                summary.explicit += 1
            elif status == "likely":
                summary.likely += 1
            else:
                summary.unsupported += 1
        summaries.append(summary)

    dependency_check = _check_dependencies(claims, numbers)
    unsupported = [item for item in limitations if item.status == "unsupported"]
    independent = {claim.number for claim in claims if claim.independent}

    return SupportReport(
        ok=not any(item.claim in independent for item in unsupported),
        unsupported_count=len(unsupported),
        claims=summaries,
        limitations=limitations,
        dependency_check=dependency_check,
        paragraphs=len(paragraphs),
    )


def _check_dependencies(claims: list[ParsedClaim], numbers: set[int]) -> DependencyCheck:
    issues: list[DependencyIssue] = []
    for claim in claims:
        for referenced in claim.depends_on:
            if referenced not in numbers:
                issues.append(
                    DependencyIssue(
                        claim=claim.number,
                        refers_to=referenced,
                        problem=f"claim {referenced} does not exist in this claim set",
                    )
                )
            elif referenced >= claim.number:
                issues.append(
                    DependencyIssue(
                        claim=claim.number,
                        refers_to=referenced,
                        problem=(
                            "a dependent claim must refer to a lower-numbered claim "
                            f"(claim {claim.number} refers to claim {referenced})"
                        ),
                    )
                )
    return DependencyCheck(ok=not issues, issues=issues)


def _is_independent(summaries: list[ClaimSummary], number: int) -> bool:
    return any(summary.claim == number and summary.independent for summary in summaries)


def _cell(text: str, limit: int = 110) -> str:
    flat = re.sub(r"\s+", " ", text or "").replace("|", "\\|").strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------


def write_support_check(
    report: SupportReport,
    directory: Path | str,
    stem: str = "support-check",
    title: str = "Claim support check",
) -> tuple[Path, Path]:
    """Write `<stem>.json` and `<stem>.md` into `directory`. Returns both paths."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / f"{stem}.json"
    md_path = target / f"{stem}.md"
    json_path.write_text(report.to_json() + "\n", encoding="utf-8")
    md_path.write_text(report.to_markdown(title), encoding="utf-8")
    return json_path, md_path
