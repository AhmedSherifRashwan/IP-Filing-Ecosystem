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
    "coerce_envelope",
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
_H2_RE: Final[re.Pattern[str]] = re.compile(r"^#{1,3}\s+(.+?)\s*$")
_BOLD_LABEL_RE: Final[re.Pattern[str]] = re.compile(r"^\*\*([A-Za-z][A-Za-z &/-]{2,40})\*\*:?\s*$")
# Canonical section names and the spellings models use for them. Matching is
# case-insensitive after stripping trailing colons and numbering.
_SECTION_ALIASES: Final[dict[str, str]] = {
    "deliverable": "Deliverable",
    "deliverables": "Deliverable",
    "output": "Deliverable",
    "rationale": "Rationale",
    "reasoning": "Rationale",
    "assumptions": "Assumptions",
    "assumption": "Assumptions",
    "open questions": "Open Questions",
    "open question": "Open Questions",
    "questions": "Open Questions",
    "findings": "Findings",
    "corrections": "Corrections",
    "correction": "Corrections",
}


def _canonical_heading(raw: str) -> str:
    text = raw.strip().strip("*_`").rstrip(":").strip()
    text = re.sub(r"^\d+[.)]\s*", "", text)
    return _SECTION_ALIASES.get(text.lower(), text)


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

    warnings: list[str] = Field(default_factory=list)

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
            label = None if heading else _BOLD_LABEL_RE.match(line)
            title = heading.group(1) if heading else (label.group(1) if label else None)
            # Only H2 opens a top-level section; deeper headings and bold labels
            # open one only when they spell a contract section name (a model
            # that wrote "### Rationale" or "**Assumptions**" meant the section).
            if title is not None:
                canonical = _canonical_heading(title)
                is_h2 = bool(heading) and line.startswith("## ")
                if is_h2 or canonical in _SECTION_ALIASES.values():
                    if current is not None:
                        sections[current] = "\n".join(buffer).strip()
                    current = canonical if canonical in _SECTION_ALIASES.values() else title.strip()
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


_SEVERITY_ALIASES: Final[dict[str, str]] = {
    "critical": "critical",
    "blocker": "critical",
    "severe": "critical",
    "fatal": "critical",
    "high": "high",
    "major": "high",
    "serious": "high",
    "significant": "high",
    "medium": "medium",
    "moderate": "medium",
    "med": "medium",
    "normal": "medium",
    "low": "low",
    "minor": "low",
    "trivial": "low",
    "info": "low",
    "informational": "low",
}
_CATEGORY_ALIASES: Final[dict[str, str]] = {
    "novelty": "novelty",
    "inventive-step": "novelty",
    "inventive step": "novelty",
    "obviousness": "novelty",
    "non-obviousness": "novelty",
    "anticipation": "novelty",
    "prior-art": "novelty",
    "prior art": "novelty",
    "enablement": "enablement",
    "written-description": "enablement",
    "written description": "enablement",
    "sufficiency": "enablement",
    "clarity": "enablement",
    "design-around": "design-around",
    "design around": "design-around",
    "detectability": "design-around",
    "enforcement": "design-around",
    "infringement": "design-around",
    "support": "support",
    "added-matter": "support",
    "added matter": "support",
    "priority": "support",
    "amendment": "support",
    "basis": "support",
    "formal": "formal",
    "formality": "formal",
    "consistency": "formal",
    "terminology": "formal",
    "numerals": "formal",
    "format": "formal",
}
_TEXT_KEYS: Final[tuple[str, ...]] = (
    "text",
    "assumption",
    "question",
    "statement",
    "description",
    "content",
    "detail",
    "note",
    "q",
    "item",
    "value",
    "summary",
    "message",
)


def _first_str(obj: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[;\n]+", value) if part.strip()]
    return [value]


def _str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in _as_list(value) if str(v).strip()]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1", "y", "blocking"}


def _text_item(
    raw: Any, prefix: str, index: int, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """`{id, text, ...}` from a string, a dict with a differently named text key, or junk."""
    item: dict[str, Any]
    if isinstance(raw, dict):
        item = dict(raw)
        text = _first_str(item, *_TEXT_KEYS) or json.dumps(
            {k: v for k, v in item.items() if k != "id"}
        )
        item["text"] = text
    else:
        item = {"text": str(raw).strip()}
    if not str(item.get("id") or "").strip():
        item["id"] = f"{prefix}{index}"
    item["id"] = str(item["id"])
    if extra:
        for key, default in extra.items():
            item.setdefault(key, default)
    return item


def coerce_envelope(data: dict[str, Any], kind: str) -> tuple[dict[str, Any], list[str]]:
    """Repair the variations models make in the envelope before validation.

    Nothing here invents content: strings become `{text}` objects, alternative
    key names are mapped, enumerations are normalised to the contract's
    vocabulary, missing lists become empty lists, and anything that still
    cannot be read is dropped item by item with a warning. The reply is not
    rejected for a spelling; it is rejected only when no envelope exists.
    """
    out = dict(data)
    warnings: list[str] = []

    status = str(out.get("status") or "").strip().lower()
    if status not in {"complete", "partial"}:
        warnings.append(f"status {out.get('status')!r} read as 'partial'")
        out["status"] = "partial" if status else "complete"
    out["prompt"] = str(out.get("prompt") or "")

    fixed_assumptions = []
    for i, raw in enumerate(_as_list(out.get("assumptions")), 1):
        item = _text_item(raw, "A", i)
        item["blocking"] = _as_bool(item.get("blocking", False))
        fixed_assumptions.append(item)
    if out.get("assumptions") is not None and not all(
        isinstance(x, dict) and "text" in x for x in _as_list(out.get("assumptions"))
    ):
        warnings.append("assumptions normalised to {id, text, blocking}")
    out["assumptions"] = fixed_assumptions

    fixed_questions = []
    for i, raw in enumerate(_as_list(out.get("open_questions")), 1):
        item = _text_item(raw, "Q", i)
        item["blocks"] = _str_list(item.get("blocks"))
        fixed_questions.append(item)
    out["open_questions"] = fixed_questions

    if kind in REVIEW_KINDS:
        findings = []
        for i, raw in enumerate(_as_list(out.get("findings")), 1):
            f = dict(raw) if isinstance(raw, dict) else {"title": str(raw)}
            f["title"] = (
                _first_str(f, "title", "summary", "name", "issue", "text") or f"Finding {i}"
            )
            f["attack"] = (
                _first_str(f, "attack", "objection", "analysis", "reason", "text", "description")
                or f["title"]
            )
            f["least_narrowing_action"] = (
                _first_str(
                    f,
                    "least_narrowing_action",
                    "action",
                    "correction",
                    "fix",
                    "recommendation",
                    "proposed_action",
                )
                or "see attack"
            )
            sev = str(f.get("severity") or "").strip().lower()
            f["severity"] = _SEVERITY_ALIASES.get(sev, "medium")
            if sev and sev not in _SEVERITY_ALIASES:
                warnings.append(f"finding {i}: severity {sev!r} read as medium")
            cat = str(f.get("category") or "").strip().lower()
            # Unknown categories become formal: the one category that never
            # drives a loop-back, so a misspelling cannot trigger a re-draft.
            f["category"] = _CATEGORY_ALIASES.get(cat, "formal")
            if cat and cat not in _CATEGORY_ALIASES:
                warnings.append(f"finding {i}: category {cat!r} read as {f['category']}")
            f["claim_refs"] = _str_list(f.get("claim_refs") or f.get("claims") or f.get("claim"))
            f["spec_refs"] = _str_list(f.get("spec_refs") or f.get("paragraphs") or f.get("refs"))
            tgt = str(f.get("suggested_target") or "").strip()
            f["suggested_target"] = tgt if tgt in {"P3", "P4", "P5", "in-place"} else "in-place"
            f["requires_new_disclosure"] = _as_bool(f.get("requires_new_disclosure", False))
            f["source_prompt"] = str(f.get("source_prompt") or out["prompt"])
            f["id"] = str(f.get("id") or f"F-{out['prompt']}-{i}")
            findings.append(f)
        out["findings"] = findings

        corrections = []
        for raw in _as_list(out.get("corrections")):
            if not isinstance(raw, dict):
                warnings.append("a non-object correction was dropped")
                continue
            c = dict(raw)
            tgt = str(c.get("target") or "").strip().lower()
            c["target"] = "claims" if "claim" in tgt else "description"
            c["location"] = _first_str(c, "location", "where", "claim", "paragraph") or ""
            c["from"] = _first_str(c, "from", "from_text", "old", "original", "before") or ""
            c["to"] = _first_str(c, "to", "to_text", "new", "replacement", "after") or ""
            c["reason"] = _first_str(c, "reason", "why", "rationale") or ""
            corrections.append(c)
        out["corrections"] = corrections

        gate14 = []
        for i, raw in enumerate(_as_list(out.get("gate14")), 1):
            g = dict(raw) if isinstance(raw, dict) else {"answer": str(raw)}
            try:
                g["q"] = int(str(g.get("q") or g.get("question") or i).strip().split()[0])
            except ValueError:
                g["q"] = i
            ans = str(g.get("answer") or "").strip().lower()
            g["answer"] = (
                "yes" if ans.startswith("y") else "no" if ans.startswith("n") else "unclear"
            )
            g["evidence"] = _first_str(g, "evidence", "reason", "text") or ""
            gate14.append(g)
        out["gate14"] = gate14
    else:
        terms = []
        for raw in _as_list(out.get("new_terms")):
            t = dict(raw) if isinstance(raw, dict) else {"term": str(raw)}
            t["term"] = _first_str(t, "term", "text", "name") or ""
            if not t["term"]:
                continue
            t["reason"] = _first_str(t, "reason", "why", "justification") or "not stated"
            t["maps_to"] = _first_str(t, "maps_to", "preferred", "locked_term")
            terms.append(t)
        out["new_terms"] = terms
        refs = []
        for raw in _as_list(out.get("support_refs")):
            r = dict(raw) if isinstance(raw, dict) else {"item": str(raw), "support": ""}
            r["item"] = _first_str(r, "item", "limitation", "claim", "element") or ""
            r["support"] = _first_str(r, "support", "passage", "paragraph", "where") or ""
            if r["item"]:
                refs.append(r)
        out["support_refs"] = refs
        reqs = []
        for raw in _as_list(out.get("disclosure_requests")):
            d = dict(raw) if isinstance(raw, dict) else {"limitation": str(raw)}
            d["limitation"] = _first_str(d, "limitation", "claim", "item", "text") or ""
            d["needed_disclosure"] = (
                _first_str(d, "needed_disclosure", "needed", "disclosure", "request") or ""
            )
            if d["limitation"]:
                reqs.append(d)
        out["disclosure_requests"] = reqs
        dis = []
        for raw in _as_list(out.get("open_disagreements")):
            d = dict(raw) if isinstance(raw, dict) else {"topic": str(raw)}
            d["topic"] = _first_str(d, "topic", "issue", "text") or ""
            d["position"] = _first_str(d, "position", "view", "reason") or ""
            if d["topic"]:
                dis.append(d)
        out["open_disagreements"] = dis
        out["narrowing_suggested"] = _str_list(out.get("narrowing_suggested"))
    return out, warnings


def _synthesised_envelope(kind: str, sections: dict[str, str]) -> dict[str, Any]:
    """An envelope built from the markdown when the model wrote none."""
    bullets = lambda name: [  # noqa: E731
        re.sub(r"^[-*\d.)\s]+", "", line).strip()
        for line in sections.get(name, "").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    base: dict[str, Any] = {
        "prompt": "",
        "status": "partial",
        "assumptions": bullets("Assumptions")[:40],
        "open_questions": bullets("Open Questions")[:40],
    }
    if kind in REVIEW_KINDS:
        base.update({"findings": [], "corrections": [], "gate14": []})
    else:
        base.update({"new_terms": [], "support_refs": []})
    return base


def parse_reply(text: str, kind: str) -> ParsedReply:
    """Read one agent reply against the contract for its prompt kind.

    Tolerant by design: headings are matched loosely, the envelope is repaired
    before validation, and a missing envelope is synthesised from the markdown.
    `ContractError` is raised only when nothing usable came back: an empty
    reply, or a drafting reply with no deliverable text at all. Everything
    that was repaired is listed in `warnings` and recorded on the output.
    """
    raw = text or ""
    if not raw.strip():
        raise ContractError("the reply was empty", missing=["everything"])

    model = envelope_model_for(kind)
    envelope_data, start, _ = extract_last_json_block(raw)
    if envelope_data is None and model is not None:
        # A bare trailing object without a fence still counts.
        recovered = extract_json_object(raw[-60_000:])
        if isinstance(recovered, dict) and ("assumptions" in recovered or "findings" in recovered):
            envelope_data = recovered
            start = raw.rfind("{" if raw.rstrip().endswith("}") else "```")
    body_md = (raw[:start] if start >= 0 else raw).strip()
    sections = split_sections(body_md)
    warnings: list[str] = []

    if kind in DRAFTING_KINDS:
        if not sections.get("Deliverable"):
            other = {k: v for k, v in sections.items() if k not in REQUIRED_DRAFTING_SECTIONS and v}
            if other or (not sections and body_md):
                # The model wrote its deliverable without the heading (or under
                # its own). Everything that is not a contract section is it.
                sections["Deliverable"] = "\n\n".join(
                    [body_md] if not sections else [f"### {k}\n\n{v}" for k, v in other.items()]
                )
                warnings.append(
                    "no `## Deliverable` heading; the unlabelled text was taken as the deliverable"
                )
            else:
                raise ContractError(
                    "the `## Deliverable` section is missing or empty", missing=["## Deliverable"]
                )
        for name in ("Rationale", "Assumptions", "Open Questions"):
            if not sections.get(name):
                sections.setdefault(name, "")
                warnings.append(f"`## {name}` section missing; treated as empty")
    elif kind in REVIEW_KINDS:
        if not sections.get("Findings"):
            other = {
                k: v
                for k, v in sections.items()
                if k not in ("Corrections", "Assumptions", "Open Questions") and v
            }
            if other or (not sections and body_md):
                sections["Findings"] = (
                    body_md
                    if not sections
                    else "\n\n".join(f"### {k}\n\n{v}" for k, v in other.items())
                )
                warnings.append(
                    "no `## Findings` heading; the unlabelled text was taken as the findings"
                )
            elif envelope_data is None:
                raise ContractError(
                    "the `## Findings` section is missing or empty", missing=["## Findings"]
                )

    envelope: DraftingEnvelope | ReviewEnvelope | None = None
    if model is not None:
        if envelope_data is None:
            envelope_data = _synthesised_envelope(kind, sections)
            warnings.append("no JSON envelope; one was synthesised from the markdown sections")
        coerced, notes = coerce_envelope(envelope_data, kind)
        warnings.extend(notes)
        try:
            envelope = model.model_validate(coerced)  # type: ignore[assignment]
        except ValidationError as exc:
            # Last resort: drop the items that still fail, keep the rest.
            bad_paths = {tuple(e["loc"][:2]) for e in exc.errors() if len(e["loc"]) >= 2}
            for key, idx in sorted(bad_paths, key=lambda p: str(p), reverse=True):
                if not isinstance(key, str) or not isinstance(idx, int):
                    continue
                items = coerced.get(key)
                if isinstance(items, list) and idx < len(items):
                    items.pop(idx)
                    warnings.append(f"{key}[{idx}] could not be read and was dropped")
            try:
                envelope = model.model_validate(coerced)  # type: ignore[assignment]
            except ValidationError as exc2:
                raise ContractError(
                    f"the JSON envelope could not be read even after repair:\n{_readable(exc2)}",
                    missing=["valid envelope"],
                ) from exc2
        envelope_data = coerced

    return ParsedReply(
        body_md=body_md,
        envelope=envelope,
        sections=sections,
        envelope_raw=envelope_data,
        warnings=warnings,
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
