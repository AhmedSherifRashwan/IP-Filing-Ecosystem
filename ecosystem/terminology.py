"""Terminology lock: parse the Prompt-4 table, then police every drafting output.

Inconsistent language is the cheapest way to lose claim scope, so once the human
locks the table at Gate 1 the check is deterministic Python, not an instruction
an agent may forget. Two rules bite:

* **avoid-hit** - a term the table says narrows scope appears in the Deliverable.
* **undeclared-synonym** - a synonym is used where the preferred term is absent,
  and the agent did not declare it in `new_terms` with a reason.

A declared new term is not a violation; it is *drift*, appended to `drift.jsonl`
so the human can see the vocabulary moving.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, Field

from ecosystem.contracts import DraftingEnvelope

log = logging.getLogger("ipfe")

__all__ = [
    "LockedTable",
    "TermReport",
    "TermRow",
    "Violation",
    "append_drift",
    "check_output",
    "parse_locked",
]

# Kinds whose output produces filing text: an undeclared synonym there is a
# violation. Analysis prose is exploratory, so the same hit is a warning.
DRAFTING_KINDS: Final[frozenset[str]] = frozenset(
    {"drafting", "compose", "revise", "formal-fix"}
)

_EMPTY_CELL: Final[frozenset[str]] = frozenset(
    {"", "-", "--", "—", "–", "n/a", "na", "none", "no", "false", "nil", "tbd"}
)
_TRUE_CELL: Final[frozenset[str]] = frozenset({"yes", "y", "true", "✓", "x", "required", "1"})

# Header keyword -> field. Order matters: "first figure" style compound headers
# are matched by the more specific keyword first.
_COLUMN_KEYWORDS: Final[tuple[tuple[str, str], ...]] = (
    ("preferred", "preferred"),
    ("synonym", "synonyms"),
    ("genus", "genus"),
    ("broader", "genus"),
    ("species", "species"),
    ("narrower", "species"),
    ("function", "functional"),
    ("avoid", "avoid"),
    ("defin", "needs_definition"),
)

_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[;,]|(?:\s+/\s+)")
_CONTEXT_CHARS: Final[int] = 60


class TermRow(BaseModel):
    """One row of the locked terminology table."""

    preferred: str
    synonyms: list[str] = Field(default_factory=list)
    genus: list[str] = Field(default_factory=list)
    species: list[str] = Field(default_factory=list)
    functional: str = ""
    avoid: list[str] = Field(default_factory=list)
    needs_definition: bool = False
    needs_definition_note: str = ""


class LockedTable(BaseModel):
    """The locked vocabulary. `source_sha256` ties a check back to the exact lock."""

    rows: list[TermRow] = Field(default_factory=list)
    source_sha256: str | None = None

    def __len__(self) -> int:
        return len(self.rows)

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, data: str | bytes | dict[str, Any]) -> LockedTable:
        if isinstance(data, str | bytes):
            data = json.loads(data)
        return cls.model_validate(data)


class Violation(BaseModel):
    term: str
    rule: str
    row: str
    context: str = ""


class DriftRecord(BaseModel):
    term: str
    maps_to: str | None = None
    reason: str = ""
    prompt: str | None = None
    at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


class TermReport(BaseModel):
    violations: list[Violation] = Field(default_factory=list)
    drift: list[DriftRecord] = Field(default_factory=list)
    warnings: list[Violation] = Field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.violations)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_locked(md_text: str, source_sha256: str | None = None) -> LockedTable:
    """Parse the first markdown table that has a `preferred`-ish column.

    Tolerant by design: the table is written by a model and then edited by a
    human at Gate 1. Columns are found by header keyword, in any order, and
    unknown columns are ignored rather than shifting the mapping.
    """
    table = _first_table_with_preferred(md_text or "")
    if table is None:
        raise ValueError(
            "no terminology table found: expected a markdown table with a "
            "column whose header contains 'preferred'"
        )
    header, body = table
    mapping = _map_columns(header)

    rows: list[TermRow] = []
    for cells in body:
        values = {field: _cell(cells, index) for field, index in mapping.items()}
        preferred = values.get("preferred", "").strip()
        if not preferred or _is_empty(preferred):
            continue
        note = values.get("needs_definition", "")
        rows.append(
            TermRow(
                preferred=_clean(preferred),
                synonyms=_split(values.get("synonyms", "")),
                genus=_split(values.get("genus", "")),
                species=_split(values.get("species", "")),
                functional=_clean(values.get("functional", "")),
                avoid=_split(values.get("avoid", "")),
                needs_definition=_truthy(note),
                needs_definition_note=_clean(note),
            )
        )
    if not rows:
        raise ValueError("terminology table has a header but no usable rows")
    return LockedTable(rows=rows, source_sha256=source_sha256)


def _first_table_with_preferred(text: str) -> tuple[list[str], list[list[str]]] | None:
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if "|" not in lines[i]:
            i += 1
            continue
        header = _row_cells(lines[i])
        if (
            i + 1 < len(lines)
            and _is_separator(lines[i + 1])
            and any(re.search(r"preferred", cell, re.I) for cell in header)
        ):
            body: list[list[str]] = []
            j = i + 2
            while j < len(lines) and "|" in lines[j]:
                if not _is_separator(lines[j]):
                    body.append(_row_cells(lines[j]))
                j += 1
            return header, body
        i += 1
    return None


def _row_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"[\s|:\-]+", line.strip())) and "-" in line


def _map_columns(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header):
        lowered = cell.lower()
        for keyword, fieldname in _COLUMN_KEYWORDS:
            if keyword in lowered and fieldname not in mapping:
                mapping[fieldname] = index
                break
    return mapping


def _cell(cells: list[str], index: int) -> str:
    return cells[index] if 0 <= index < len(cells) else ""


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("**", "").replace("`", "")).strip()


def _is_empty(value: str) -> bool:
    return _clean(value).lower() in _EMPTY_CELL


def _truthy(value: str) -> bool:
    cleaned = _clean(value).lower()
    if cleaned in _TRUE_CELL:
        return True
    return bool(cleaned) and cleaned not in _EMPTY_CELL


def _split(value: str) -> list[str]:
    if _is_empty(value):
        return []
    parts = [_clean(part) for part in _SPLIT_RE.split(value)]
    return [part for part in parts if part and not _is_empty(part)]


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


def _mentions(text: str, term: str) -> re.Match[str] | None:
    """Word-boundary, case-insensitive. Multi-word terms match across single spaces."""
    if not term.strip():
        return None
    pattern = r"\s+".join(re.escape(word) for word in term.split())
    return re.search(rf"(?<!\w){pattern}(?!\w)", text, re.IGNORECASE)


def _context(text: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - _CONTEXT_CHARS)
    end = min(len(text), match.end() + _CONTEXT_CHARS)
    snippet = text[start:end].replace("\n", " ")
    return re.sub(r"\s+", " ", snippet).strip()


def check_output(
    deliverable_md: str,
    envelope: DraftingEnvelope | None,
    table: LockedTable,
    kind: str,
    prompt_id: str | None = None,
) -> TermReport:
    """Check one Deliverable against the lock.

    Only the Deliverable is checked: Rationale and Assumptions are allowed to
    discuss the words the drafting must not use.
    """
    text = deliverable_md or ""
    report = TermReport()
    new_terms = envelope.new_terms if envelope else []
    declared = {term.term.strip().lower(): term for term in new_terms}
    hard = kind in DRAFTING_KINDS

    for row in table.rows:
        for avoided in row.avoid:
            match = _mentions(text, avoided)
            if match is None:
                continue
            violation = Violation(
                term=avoided,
                rule="avoid-hit",
                row=row.preferred,
                context=_context(text, match),
            )
            # An avoid-list hit narrows scope whatever the kind; analysis only warns.
            (report.violations if hard else report.warnings).append(violation)

        if _mentions(text, row.preferred) is not None:
            continue
        for synonym in row.synonyms:
            match = _mentions(text, synonym)
            if match is None:
                continue
            if synonym.strip().lower() in declared:
                continue
            violation = Violation(
                term=synonym,
                rule="undeclared-synonym",
                row=row.preferred,
                context=_context(text, match),
            )
            (report.violations if hard else report.warnings).append(violation)

    for term in new_terms:
        reason = (term.reason or "").strip()
        if not reason:
            report.violations.append(
                Violation(
                    term=term.term,
                    rule="new-term-without-reason",
                    row=term.maps_to or "",
                    context="",
                )
            )
            continue
        report.drift.append(
            DriftRecord(
                term=term.term,
                maps_to=term.maps_to,
                reason=reason,
                prompt=prompt_id,
            )
        )

    return report


def append_drift(path: Path | str, records: list[DriftRecord]) -> int:
    """Append drift to `terminology/drift.jsonl`. Append-only, like the event log."""
    if not records:
        return 0
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.model_dump_json() + "\n")
    return len(records)
