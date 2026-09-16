"""Reference-numeral registry (Prompt 20a) and its cross-check.

Numerals are the one place where a drafting error is both trivially detectable
and expensive at examination: a numeral in the description that no figure shows,
or a claimed component with no numeral at all. The cross-check is deterministic
and never auto-fixes - its output is fed to Prompt 38 for a human-readable
judgement (plan s3.10).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Final

from pydantic import BaseModel, Field

log = logging.getLogger("ipfe")

__all__ = ["CrossCheck", "Registry", "RegistryEntry", "cross_check", "parse_registry"]

# Header keyword -> field. `first` is tested before the generic figure column.
_NUMERAL_KEYS: Final[tuple[str, ...]] = ("numeral", "ref", "no.", "number")
_COMPONENT_KEYS: Final[tuple[str, ...]] = ("component", "part", "element", "name", "feature")

# Patent numerals are conventionally two or more digits with an optional letter.
_NUMERAL_TOKEN: Final[str] = r"\d{2,4}[a-zA-Z]?"
# A numeral counts as "used in the text" only when it trails a word, which is how
# patent prose introduces one ("the housing 10"). Bare numbers are measurements.
_TEXT_NUMERAL_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?<![\w.])([A-Za-z][\w-]*)[ \t]+({_NUMERAL_TOKEN})(?!\w)(?![.,]?\d)(?!%)"
)
# Words that legitimately precede a number without introducing a numeral.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "fig",
        "figs",
        "figure",
        "figures",
        "claim",
        "claims",
        "step",
        "steps",
        "page",
        "paragraph",
        "para",
        "table",
        "item",
        "no",
        "number",
        "section",
        "example",
        "embodiment",
        "about",
        "approximately",
        "least",
        "most",
        "than",
        "to",
        "of",
        "and",
        "or",
        "between",
        "from",
        "up",
        "us",
        "ep",
        "wo",
        "gb",
        "version",
        "v",
    }
)
_FIG_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"(\d+[A-Za-z]?)")


class RegistryEntry(BaseModel):
    numeral: str
    component: str
    first_figure: str = ""
    figures: list[str] = Field(default_factory=list)


class Registry(BaseModel):
    entries: list[RegistryEntry] = Field(default_factory=list)
    source: str | None = None

    def __len__(self) -> int:
        return len(self.entries)

    def by_numeral(self) -> dict[str, RegistryEntry]:
        return {entry.numeral: entry for entry in self.entries}

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, data: str | bytes | dict[str, Any]) -> Registry:
        if isinstance(data, str | bytes):
            data = json.loads(data)
        return cls.model_validate(data)


class CrossCheck(BaseModel):
    """Result of comparing the registry with the finished description and claims."""

    numerals_in_text_not_in_registry: list[str] = Field(default_factory=list)
    registry_numerals_not_in_figures: list[str] = Field(default_factory=list)
    claim_components_without_numeral: list[str] = Field(default_factory=list)
    ok: bool = True

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, data: str | bytes | dict[str, Any]) -> CrossCheck:
        if isinstance(data, str | bytes):
            data = json.loads(data)
        return cls.model_validate(data)


def parse_registry(md_text: str, source: str | None = None) -> Registry:
    """Parse the Prompt-20a table: the first table with a numeral column."""
    table = _first_table_with_numeral(md_text or "")
    if table is None:
        raise ValueError(
            "no reference-numeral table found: expected a markdown table with a "
            "column whose header contains 'numeral'"
        )
    header, body = table
    mapping = _map_columns(header)

    entries: list[RegistryEntry] = []
    for cells in body:
        numeral = _clean(_cell(cells, mapping.get("numeral", -1)))
        component = _clean(_cell(cells, mapping.get("component", -1)))
        if not numeral or not re.fullmatch(r"\d+[a-zA-Z]?", numeral):
            continue
        first_figure = _normalise_figure(_cell(cells, mapping.get("first_figure", -1)))
        figures = _figures(_cell(cells, mapping.get("figures", -1)))
        # A first figure is by definition a figure the numeral appears in; agents
        # routinely list only the *subsequent* ones in the figures column.
        if first_figure and first_figure not in figures:
            figures.insert(0, first_figure)
        entries.append(
            RegistryEntry(
                numeral=numeral,
                component=component,
                first_figure=first_figure,
                figures=figures,
            )
        )
    if not entries:
        raise ValueError("reference-numeral table has a header but no usable rows")
    return Registry(entries=entries, source=source)


def cross_check(registry: Registry, description_md: str, claims_md: str) -> CrossCheck:
    """Three deterministic checks. Anything non-empty means a human must look."""
    known = {entry.numeral for entry in registry.entries}

    in_text: set[str] = set()
    for match in _TEXT_NUMERAL_RE.finditer(description_md or ""):
        word = match.group(1).lower().rstrip(".")
        if word in _STOPWORDS:
            continue
        in_text.add(match.group(2))
    missing_from_registry = sorted(in_text - known, key=_sort_key)

    not_in_figures = sorted(
        (entry.numeral for entry in registry.entries if not entry.figures), key=_sort_key
    )

    claims = claims_md or ""
    without_numeral: list[str] = []
    for entry in registry.entries:
        if not entry.component:
            continue
        if _mentions(claims, entry.component) and not _mentions_numeral(claims, entry.numeral):
            without_numeral.append(entry.component)

    result = CrossCheck(
        numerals_in_text_not_in_registry=missing_from_registry,
        registry_numerals_not_in_figures=not_in_figures,
        claim_components_without_numeral=sorted(set(without_numeral)),
    )
    result.ok = not (
        result.numerals_in_text_not_in_registry
        or result.registry_numerals_not_in_figures
        or result.claim_components_without_numeral
    )
    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _first_table_with_numeral(text: str) -> tuple[list[str], list[list[str]]] | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "|" not in line or i + 1 >= len(lines) or not _is_separator(lines[i + 1]):
            continue
        header = _row_cells(line)
        if not any(any(key in cell.lower() for key in _NUMERAL_KEYS) for cell in header):
            continue
        body: list[list[str]] = []
        j = i + 2
        while j < len(lines) and "|" in lines[j]:
            if not _is_separator(lines[j]):
                body.append(_row_cells(lines[j]))
            j += 1
        return header, body
    return None


def _map_columns(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header):
        lowered = cell.lower()
        if "numeral" not in mapping and any(key in lowered for key in _NUMERAL_KEYS):
            mapping["numeral"] = index
            continue
        if "component" not in mapping and any(key in lowered for key in _COMPONENT_KEYS):
            mapping["component"] = index
            continue
        if "fig" in lowered:
            if "first" in lowered or "intro" in lowered:
                mapping.setdefault("first_figure", index)
            else:
                mapping.setdefault("figures", index)
    return mapping


def _row_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"[\s|:\-]+", line.strip())) and "-" in line


def _cell(cells: list[str], index: int) -> str:
    return cells[index] if 0 <= index < len(cells) else ""


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("**", "").replace("`", "")).strip()


def _normalise_figure(value: str) -> str:
    """`FIG. 2` / `Fig 2` / `2` all mean figure 2. Stored as the bare token."""
    match = _FIG_NUMBER_RE.search(_clean(value))
    return match.group(1) if match else ""


def _figures(value: str) -> list[str]:
    cleaned = _clean(value)
    if not cleaned or cleaned.lower() in {"-", "--", "—", "n/a", "na", "none"}:
        return []
    out: list[str] = []
    for part in re.split(r"[;,/]| and ", cleaned):
        number = _normalise_figure(part)
        if number and number not in out:
            out.append(number)
    return out


def _mentions(text: str, term: str) -> bool:
    pattern = r"\s+".join(re.escape(word) for word in term.split())
    return bool(re.search(rf"(?<!\w){pattern}(?!\w)", text, re.IGNORECASE))


def _mentions_numeral(text: str, numeral: str) -> bool:
    return bool(re.search(rf"(?<![\w.]){re.escape(numeral)}(?!\w)(?![.,]?\d)", text))


def _sort_key(numeral: str) -> tuple[int, str]:
    match = re.match(r"(\d+)([a-zA-Z]?)", numeral)
    return (int(match.group(1)), match.group(2)) if match else (0, numeral)
