"""The output contract every agent reply must satisfy.

A reply is markdown sections the human reads plus one trailing fenced JSON
envelope the runner acts on. Both halves are mandatory and both are validated
here: an envelope that does not parse is a failed turn, never a partially
populated object handed to the loop-back engine.

`ContractError.message` is written to be appended verbatim to a correction
prompt - the agent is told exactly what was missing.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "reply_looks_defective",
    "ContractError",
    "Correction",
    "DraftingEnvelope",
    "Finding",
    "Gate14Answer",
    "ParsedReply",
    "ReviewEnvelope",
    "Severity",
    "deliverable_only",
    "envelope_model_for",
    "extract_json_object",
    "extract_last_json_block",
    "parse_reply",
    "split_sections",
]

Severity = Literal["critical", "high", "medium", "low"]
FindingCategory = Literal["novelty", "enablement", "design-around", "support", "formal"]

# Kinds whose reply carries the drafting envelope and the four drafting H2s.
DRAFTING_KINDS: Final[frozenset[str]] = frozenset(
    {"analysis", "drafting", "compose", "revise", "formal-fix"}
)
REVIEW_KINDS: Final[frozenset[str]] = frozenset({"review"})
# Prose (gate briefs, cover memo) is for humans: no envelope, no fixed sections.
PROSE_KINDS: Final[frozenset[str]] = frozenset({"prose"})

REQUIRED_DRAFTING_SECTIONS: Final[tuple[str, ...]] = (
    "Deliverable",
    "Rationale",
    "Assumptions",
    "Open Questions",
)
REQUIRED_REVIEW_SECTIONS: Final[tuple[str, ...]] = ("Findings",)

_JSON_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n?```", re.DOTALL
)
_H2_RE: Final[re.Pattern[str]] = re.compile(r"^##\s+(.+?)\s*$")
_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(```|~~~)")


class ContractError(ValueError):
    """The reply does not satisfy the output contract for its kind."""

    def __init__(self, message: str, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.missing = missing or []

    def correction_text(self) -> str:
        """Appended to the original prompt for the one correction turn."""
        return (
            "Your previous reply was rejected by the output contract:\n"
            f"{self.message}\n\n"
            "Resend the COMPLETE reply: the required markdown sections in order, "
            "then exactly one closing fenced ```json block containing the full "
            "envelope. Do not apologise, do not send a diff, do not omit sections "
            "that were already correct."
        )


# ---------------------------------------------------------------------------
# Envelope models
# ---------------------------------------------------------------------------


class _Envelope(BaseModel):
    """Agents add fields; extras are kept so nothing an agent said is silently lost."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Assumption(_Envelope):
    id: str
    text: str
    blocking: bool = False


class OpenQuestion(_Envelope):
    id: str
    text: str
    blocks: list[str] = Field(default_factory=list)


class NewTerm(_Envelope):
    term: str
    maps_to: str | None = None
    reason: str


class SupportRef(_Envelope):
    item: str
    support: str


class DisclosureRequest(_Envelope):
    limitation: str
    needed_disclosure: str


class OpenDisagreement(_Envelope):
    topic: str
    position: str


class DraftingEnvelope(_Envelope):
    """Envelope for analysis / drafting / compose / revise turns."""

    prompt: str
    status: Literal["complete", "partial"]
    assumptions: list[Assumption] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    new_terms: list[NewTerm] = Field(default_factory=list)
    support_refs: list[SupportRef] = Field(default_factory=list)
    # Agent 6 -> Agent 5 in the inner loop.
    disclosure_requests: list[DisclosureRequest] = Field(default_factory=list)
    open_disagreements: list[OpenDisagreement] = Field(default_factory=list)
    narrowing_suggested: list[str] = Field(default_factory=list)


class Finding(_Envelope):
    """One reviewer objection. `category` decides where the loop-back engine routes it."""

    id: str
    source_prompt: str
    category: FindingCategory
    severity: Severity
    title: str
    claim_refs: list[str] = Field(default_factory=list)
    spec_refs: list[str] = Field(default_factory=list)
    attack: str
    least_narrowing_action: str
    requires_new_disclosure: bool = False
    suggested_target: Literal["P3", "P4", "P5", "in-place"]

    # Filled in by `loopback.aggregate`, never by an agent. A reviewer is not
    # told the other reviewer exists, so it cannot report agreement; agreement
    # is a fact about the merge, computed from two envelopes that were produced
    # independently. Defaults keep every envelope and every findings.json
    # written before the second reviewer existed valid.
    reviewers: list[str] = Field(default_factory=list)
    consensus: bool = False


class Correction(_Envelope):
    """A literal text fix from the formal passes (37 / 38 / 40a)."""

    target: Literal["claims", "description"]
    location: str
    # `from` is a Python keyword; the wire name is preserved in both directions.
    from_text: str = Field(
        validation_alias=AliasChoices("from", "from_text"),
        serialization_alias="from",
    )
    to: str
    reason: str


class Gate14Answer(_Envelope):
    q: int
    answer: Literal["yes", "no", "unclear"]
    evidence: str


class ReviewEnvelope(_Envelope):
    """Envelope for every ip-reviewer turn."""

    prompt: str
    status: Literal["complete", "partial"]
    findings: list[Finding] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)
    gate14: list[Gate14Answer] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)


class ParsedReply(BaseModel):
    """A validated reply: body for the dossier, envelope for the runner."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    body_md: str
    envelope: DraftingEnvelope | ReviewEnvelope | None = None
    sections: dict[str, str] = Field(default_factory=dict)
    envelope_raw: dict[str, Any] | None = None

    @property
    def deliverable(self) -> str:
        return self.sections.get("Deliverable", "")


# ---------------------------------------------------------------------------
# JSON extraction (ported from Nucleus app/providers/openclaw_client.py)
# ---------------------------------------------------------------------------


def extract_last_json_block(text: str) -> tuple[dict[str, Any] | None, int, int]:
    """Last fenced JSON block that parses as an object, with its span.

    Last, not first: the deliverable itself often contains example JSON, and the
    envelope is contractually the closing block.
    """
    for match in reversed(list(_JSON_FENCE_RE.finditer(text or ""))):
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed, match.start(), match.end()
    return None, -1, -1


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Recover a JSON object from model prose.

    Tries the fenced blocks last-first, then the first balanced brace span.
    Returns None rather than raising so the caller can run its correction turn.
    """
    if not text:
        return None

    parsed, _, _ = extract_last_json_block(text)
    if parsed is not None:
        return parsed

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    candidate = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return candidate if isinstance(candidate, dict) else None
    return None


# ---------------------------------------------------------------------------
# Section handling
# ---------------------------------------------------------------------------


def split_sections(body_md: str) -> dict[str, str]:
    """H2 title -> section text. Headings inside fenced code blocks are not headings."""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    fence: str | None = None
    for line in (body_md or "").splitlines():
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            token = fence_match.group(1)
            if fence is None:
                fence = token
            elif line.strip().startswith(fence):
                fence = None
        if fence is None:
            heading = _H2_RE.match(line)
            if heading:
                if current is not None:
                    sections[current] = "\n".join(buffer).strip()
                current = heading.group(1).strip()
                buffer = []
                continue
        if current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    return sections


def deliverable_only(body_md: str) -> str:
    """The `## Deliverable` section alone - what packaging and terminology checks see."""
    return split_sections(body_md).get("Deliverable", "").strip()


def envelope_model_for(kind: str) -> type[BaseModel] | None:
    if kind in REVIEW_KINDS:
        return ReviewEnvelope
    if kind in PROSE_KINDS:
        return None
    return DraftingEnvelope


# ---------------------------------------------------------------------------
# parse_reply
# ---------------------------------------------------------------------------


def parse_reply(text: str, kind: str) -> ParsedReply:
    """Validate one agent reply against the contract for its prompt kind.

    Raises `ContractError` listing every problem at once: a correction turn is
    expensive, so it must not be spent discovering the second defect.
    """
    raw = text or ""
    if not raw.strip():
        raise ContractError("the reply was empty", missing=["everything"])

    model = envelope_model_for(kind)
    envelope_data, start, _ = extract_last_json_block(raw)
    body_md = (raw[:start] if start >= 0 else raw).strip()
    sections = split_sections(body_md)

    missing: list[str] = []
    problems: list[str] = []

    required_sections = (
        REQUIRED_REVIEW_SECTIONS
        if kind in REVIEW_KINDS
        else REQUIRED_DRAFTING_SECTIONS
        if kind in DRAFTING_KINDS
        else ()
    )
    for name in required_sections:
        if not sections.get(name):
            missing.append(f"## {name}")
            problems.append(f"the `## {name}` section is missing or empty")

    envelope: DraftingEnvelope | ReviewEnvelope | None = None
    if model is not None:
        if envelope_data is None:
            missing.append("json envelope")
            problems.append(
                "no closing fenced ```json block containing the envelope object was found"
            )
        else:
            try:
                envelope = model.model_validate(envelope_data)  # type: ignore[assignment]
            except ValidationError as exc:
                missing.append("valid envelope")
                problems.append(f"the JSON envelope failed validation:\n{_readable(exc)}")

    if problems:
        raise ContractError("; ".join(problems), missing=missing)

    return ParsedReply(
        body_md=body_md,
        envelope=envelope,
        sections=sections,
        envelope_raw=envelope_data,
    )


def _readable(exc: ValidationError) -> str:
    """Field-path + message, one per line. Pydantic's default dump is too noisy to resend."""
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)


_FIRST_HEADING_BY_KIND = {
    "review": "## Findings",
    "prose": None,
}


def reply_looks_defective(text: str, kind: str) -> str | None:
    """Why a reply cannot be a real answer, or None.

    "empty": nothing came back. "head-truncated": a long reply that does not
    open with the contract's first heading and shows no heading at all in its
    first lines - the runtime returned the tail of a multi-part answer. Both are
    transport defects to retry, not contract failures to correct.
    """
    body = (text or "").strip()
    if len(body) < 20:
        return "empty"
    first = _FIRST_HEADING_BY_KIND.get(kind, "## Deliverable")
    if first is None or len(body) < 1500:
        return None
    head = body[:400]
    if first in head or "\n## " in head or head.startswith("## ") or head.startswith("```json"):
        return None
    return "head-truncated"
