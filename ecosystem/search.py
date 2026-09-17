"""Opt-in prior-art retrieval from Google Patents.

This module is the only part of the ecosystem that talks to a third party, and
it runs only when the human asks for it (`ipfe run --search`). Three rules shape
it:

1. **Query strings leave the machine; the disclosure never does.** What is sent
   is the search strings Prompt 06 already wrote for a human searcher. Nothing
   derived from `00-intake/disclosure.md` is transmitted verbatim, and the
   disclosure text itself is never read by this module.
2. **What comes back is a candidate, not a fact.** Every file written carries a
   `MACHINE-RETRIEVED` marker in its first line, a sha256 of the page it was
   extracted from, and the queries that surfaced it. Human-supplied references
   under `00-intake/prior-art/` are never read, moved or overwritten: only
   `machine-*.md` names are written, and only those are replaced on a re-run.
3. **The network is allowed to fail.** A rate limit or a parse failure on one
   query or one patent page is logged, recorded in `SEARCH-LOG.json` and
   skipped. Only a run in which no query succeeded at all raises
   `SearchUnavailable` - a half-empty candidate set is still useful, a silently
   empty one is not.

Every network call goes through an injectable `fetch(url, headers) -> bytes`, so
the tests exercise the whole pipeline against saved fixtures without a socket.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Final

from ecosystem.dossier import Dossier, split_frontmatter

log = logging.getLogger("ipfe")

__all__ = [
    "Candidate",
    "Fetch",
    "Hit",
    "NoQueries",
    "PatentRecord",
    "Query",
    "SearchRun",
    "SearchUnavailable",
    "build_query_url",
    "fetch_patent",
    "normalise_query",
    "queries_from_markdown",
    "queries_from_phase2",
    "run_search",
    "search",
    "wire_query",
]

# A single seam for every byte that crosses the network boundary.
Fetch = Callable[[str, dict[str, str]], bytes]

QUERY_ENDPOINT: Final[str] = "https://patents.google.com/xhr/query"
PATENT_URL: Final[str] = "https://patents.google.com/patent/{number}/en"
SERVICE_NAME: Final[str] = "Google Patents (patents.google.com)"
SERVICE_HOST: Final[str] = "patents.google.com"

# Google Patents serves the XHR endpoint only to something that looks like a
# browser; a bare urllib user agent gets a 403.
QUERY_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}
PAGE_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html",
}

# The one file-name prefix this module owns. Everything else under
# `00-intake/prior-art/` belongs to the human and is never touched.
MACHINE_PREFIX: Final[str] = "machine-"
LOG_NAME: Final[str] = "SEARCH-LOG.json"
README_NAME: Final[str] = "SEARCH-README.md"

DESCRIPTION_EXCERPT_CHARS: Final[int] = 3000
MAX_QUERY_CHARS: Final[int] = 300
MAX_CPC_CODES: Final[int] = 24


class SearchUnavailable(RuntimeError):
    """The search could not be run at all. Carries a message fit for the human."""


class NoQueries(SearchUnavailable):
    """Phase 2 produced no usable search strings, so there is nothing to send."""


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Query:
    """One search string, with where it came from. `text` is what is sent."""

    text: str
    purpose: str = ""
    source: str = ""


@dataclass(frozen=True)
class Hit:
    """One result row of one query. Cheap metadata only; no page has been read."""

    number: str
    title: str
    date: str
    assignee: str
    snippet: str
    url: str
    query: str
    rank: int
    retrieved_at: str


@dataclass
class Candidate:
    """A publication after merging the hits of every query that surfaced it."""

    number: str
    title: str
    date: str
    assignee: str
    snippet: str
    url: str
    rank: int
    hit_count: int
    queries: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PatentRecord:
    """The extracted text of one patent page, plus the hash of the page itself."""

    number: str
    title: str
    abstract: str
    claims: list[str]
    description_excerpt: str
    cpc: list[str]
    dates: dict[str, str]
    url: str
    retrieved_at: str
    sha256: str


@dataclass
class SearchRun:
    """What one `ipfe search` did: counts, paths, and everything that failed."""

    matter: str
    started_at: str
    finished_at: str
    queries: list[Query]
    query_source: str | None
    candidates: list[Candidate]
    records: list[PatentRecord]
    files: list[Path]
    log_path: Path
    readme_path: Path
    errors: list[str]
    counts: dict[str, int]


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


def _default_fetch(timeout: float) -> Fetch:
    """urllib behind the `Fetch` seam. Non-200 raises; callers decide what to do."""

    def fetch(url: str, headers: dict[str, str]) -> bytes:
        if urllib.parse.urlsplit(url).scheme != "https":
            raise SearchUnavailable(f"refusing to fetch a non-https url: {url}")
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            data: bytes = response.read()
        return data

    return fetch


def _fetch_text(fetch: Fetch, url: str, headers: dict[str, str]) -> str:
    raw = fetch(url, headers)
    if not isinstance(raw, bytes | bytearray):
        raise SearchUnavailable(f"fetch() returned {type(raw).__name__}, expected bytes")
    return bytes(raw).decode("utf-8", errors="replace")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# query extraction
# ---------------------------------------------------------------------------

_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(```|~~~)")
_LIST_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_LABEL_RE: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z][A-Za-z0-9 /&-]{0,40}):\s+")
_CPC_CODE_RE: Final[re.Pattern[str]] = re.compile(r"\b[A-HY]\d{2}[A-Z]\s?\d{1,4}/\d{1,6}\b")
_BOOLEAN_RE: Final[re.Pattern[str]] = re.compile(r"(?<![\w-])(AND|OR|NEAR)(?![\w-])")
_AND_RE: Final[re.Pattern[str]] = re.compile(r"(?<![\w-])AND(?![\w-])")
_QUOTED_RE: Final[re.Pattern[str]] = re.compile(r"[\"“][^\"”]{2,}[\"”]")
_CLASS_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"\b(cpc|ipc)\s*[=:]\s*", re.IGNORECASE)
_CLASS_SPACE_RE: Final[re.Pattern[str]] = re.compile(r"\b(cpc|ipc):([A-HY]\d{2}[A-Z])\s+(\d)")
_TABLE_SEPARATOR_RE: Final[re.Pattern[str]] = re.compile(r"^[\s|:-]+$")
# Labels that are part of the query syntax, not a prose prefix to be stripped.
_KEEP_LABELS: Final[frozenset[str]] = frozenset({"cpc", "ipc", "cpc code", "ipc code", "q"})
_WIRE_CLASS_RE: Final[re.Pattern[str]] = re.compile(r"(?<![\w-])(?:cpc|ipc):", re.IGNORECASE)


def normalise_query(raw: str) -> str:
    """Turn one candidate string into Google Patents syntax, or `""` if unusable.

    Quotes are meaningful and are kept. `AND` is implicit on Google Patents, so
    it becomes a space; `OR` and `NEAR` are real operators and survive. `CPC=`
    becomes `cpc:` and the space inside a classification code that follows it is
    closed up (`cpc:F16K 15/20` would otherwise search for the literal `15/20`).
    """
    text = (raw or "").strip()
    if not text:
        return ""
    text = text.replace("`", "")
    text = _LIST_MARKER_RE.sub("", text)
    # Markdown emphasis is never query syntax; bold labels ("**Search string:** ...")
    # are the commonest way a search string arrives, so unwrap before reading the label.
    text = text.replace("**", "").replace("__", "")
    text = text.strip().strip("*_ ").strip()

    label = _LABEL_RE.match(text)
    if label and label.group(1).strip().lower() not in _KEEP_LABELS:
        stripped = text[label.end() :].strip()
        if stripped:
            text = stripped

    text = _CLASS_PREFIX_RE.sub(lambda m: f"{m.group(1).lower()}:", text)
    text = _CLASS_SPACE_RE.sub(r"\1:\2\3", text)
    text = _AND_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(" .,;")

    if len(text) < 3 or len(text) > MAX_QUERY_CHARS:
        return ""
    if not re.search(r"[A-Za-z0-9]", text):
        return ""
    return text


def _looks_like_query(text: str) -> bool:
    """A search string, not prose: it quotes, it uses an operator, or it is a code."""
    if not text or len(text) > MAX_QUERY_CHARS:
        return False
    return bool(
        _QUOTED_RE.search(text)
        or _BOOLEAN_RE.search(text)
        or _CPC_CODE_RE.search(text)
        or re.search(r"\b(cpc|ipc)\s*[=:]", text, re.IGNORECASE)
    )


def _candidate_lines(markdown: str) -> Iterator[str]:
    """Yield the fragments of a Prompt 06 deliverable that could be search strings."""
    fence: str | None = None
    for line in markdown.splitlines():
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            token = fence_match.group(1)
            if fence is None:
                fence = token
            elif line.strip().startswith(fence):
                fence = None
            continue
        if fence is not None:
            if line.strip():
                yield line.strip()
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("|"):
            if _TABLE_SEPARATOR_RE.match(stripped):
                continue
            for cell in stripped.strip("|").split("|"):
                if cell.strip():
                    yield cell.strip()
            continue
        if _LIST_MARKER_RE.match(line):
            yield stripped


def queries_from_markdown(markdown: str, max_queries: int = 12) -> list[str]:
    """Extract search strings from a Prompt 06 deliverable, in document order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in _candidate_lines(markdown):
        if not _looks_like_query(raw):
            continue
        query = normalise_query(raw)
        if not query:
            continue
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(query)
        if len(out) >= max_queries:
            break
    return out


def _latest_06_envelope(dossier: Dossier) -> tuple[Path, dict[str, Any]] | None:
    """Newest readable `06.envelope.json`, including the in-progress P2 version."""
    for version_dir in dossier.readable_versions_desc("P2", include_running=True):
        path = version_dir.path / "06.envelope.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("unreadable %s: %s", path, exc)
            continue
        if isinstance(data, dict):
            return path, data
    return None


def queries_from_phase2(dossier: Dossier, max_queries: int = 12) -> list[Query]:
    """The search strings Phase 2 produced: envelope first, deliverable second.

    `search_queries` in the Prompt 06 envelope is the structured, intended
    source. When it is absent (an older run, or an agent that only wrote prose)
    the deliverable is parsed for the strings it told the human to run, which is
    lossier but beats sending nothing.
    """
    found = _latest_06_envelope(dossier)
    if found is not None:
        path, envelope = found
        raw = envelope.get("search_queries")
        if isinstance(raw, list) and raw:
            out: list[Query] = []
            seen: set[str] = set()
            for item in raw:
                if isinstance(item, dict):
                    text = str(item.get("query") or item.get("text") or "")
                    purpose = str(item.get("purpose") or "")
                else:
                    text, purpose = str(item), ""
                query = normalise_query(text)
                if not query or query.lower() in seen:
                    continue
                seen.add(query.lower())
                out.append(Query(text=query, purpose=purpose, source=path.name))
                if len(out) >= max_queries:
                    break
            if out:
                return out
            log.warning("%s has search_queries but none of them survived normalisation", path)

    output = dossier.latest_output("P2", "06", include_running=True)
    if output is None:
        return []
    _, body = split_frontmatter(output.read_text(encoding="utf-8"))
    return [
        Query(text=text, purpose="", source=output.name)
        for text in queries_from_markdown(body, max_queries)
    ]


# ---------------------------------------------------------------------------
# query endpoint
# ---------------------------------------------------------------------------


def wire_query(query: str) -> str:
    """The form actually sent to the endpoint.

    `cpc:`/`ipc:` is a Google Patents *UI* filter, not free-text syntax: sending
    `cpc:F16K15/20` in `q=` returns zero results (measured), while the bare code
    `F16K15/20` returns the class as expected, alone or beside other terms. The
    prefix is kept in the stored query text because it says what the searcher
    meant, and stripped here on the way out. `SEARCH-LOG.json` records the full
    URL beside the query, so the wire form is never hidden.
    """
    text = _WIRE_CLASS_RE.sub("", query)
    return re.sub(r"\s+", " ", text).strip()


def build_query_url(query: str, num: int = 10) -> str:
    """The XHR endpoint takes a *doubly* encoded query string in `url=`."""
    inner = f"q={urllib.parse.quote_plus(wire_query(query))}&num={num}"
    return f"{QUERY_ENDPOINT}?url={urllib.parse.quote_plus(inner)}&exp="


def search(
    query: str,
    *,
    num: int = 10,
    fetch: Fetch | None = None,
    timeout: float = 20.0,
) -> list[Hit]:
    """Run one query. Raises `SearchUnavailable` if the reply is not usable JSON."""
    url = build_query_url(query, num)
    fetcher = fetch or _default_fetch(timeout)
    text = _fetch_text(fetcher, url, QUERY_HEADERS)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SearchUnavailable(f"{SERVICE_HOST} returned a non-JSON reply: {exc}") from exc
    if not isinstance(payload, dict):
        kind = type(payload).__name__
        raise SearchUnavailable(f"{SERVICE_HOST} returned {kind}, expected a JSON object")
    _, hits = _parse_query_payload(payload, query=query, num=num)
    return hits


def _parse_query_payload(payload: dict[str, Any], *, query: str, num: int) -> tuple[int, list[Hit]]:
    """Flatten `results.cluster[].result[].patent` into ranked hits. Tolerates gaps."""
    results = payload.get("results")
    if not isinstance(results, dict):
        return 0, []
    total = _as_int(results.get("total_num_results"))
    retrieved_at = _now()
    hits: list[Hit] = []
    rank = 0
    for cluster in _as_list(results.get("cluster")):
        if not isinstance(cluster, dict):
            continue
        for entry in _as_list(cluster.get("result")):
            if not isinstance(entry, dict):
                continue
            patent = entry.get("patent")
            if not isinstance(patent, dict):
                continue
            number = _clean(str(patent.get("publication_number") or "")).replace(" ", "")
            if not number:
                continue
            rank += 1
            hits.append(
                Hit(
                    number=number,
                    title=_clean(str(patent.get("title") or "")),
                    date=_clean(
                        str(
                            patent.get("publication_date")
                            or patent.get("grant_date")
                            or patent.get("filing_date")
                            or ""
                        )
                    ),
                    assignee=_clean(str(patent.get("assignee") or "")),
                    snippet=_clean(str(patent.get("snippet") or "")),
                    url=PATENT_URL.format(number=number),
                    query=query,
                    rank=rank,
                    retrieved_at=retrieved_at,
                )
            )
            if rank >= num:
                return total, hits
    return total, hits


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# patent page
# ---------------------------------------------------------------------------

_TAG_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")
_SCRIPT_RE: Final[re.Pattern[str]] = re.compile(
    r"<(script|style)\b.*?</\1>", re.DOTALL | re.IGNORECASE
)
_META_DESCRIPTION_RE: Final[re.Pattern[str]] = re.compile(
    r'<meta\s+name="description"\s+content="(.*?)"\s*/?>', re.DOTALL | re.IGNORECASE
)
_META_TITLE_RE: Final[re.Pattern[str]] = re.compile(
    r'<meta\s+name="DC\.title"\s+content="(.*?)"\s*/?>', re.DOTALL | re.IGNORECASE
)
_TITLE_TAG_RE: Final[re.Pattern[str]] = re.compile(
    r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE
)
_CODE_RE: Final[re.Pattern[str]] = re.compile(
    r'<span\s+itemprop="Code"[^>]*>([^<]*)</span>', re.IGNORECASE
)
_ABSTRACT_DIV_RE: Final[re.Pattern[str]] = re.compile(
    r'<div[^>]*class="abstract"[^>]*>', re.IGNORECASE
)
_CLAIM_DIV_RE: Final[re.Pattern[str]] = re.compile(
    r'<div(?=[^>]*class="claim")(?=[^>]*\b(?:id|num)=)[^>]*>', re.IGNORECASE
)
_CLAIM_TEXT_DIV_RE: Final[re.Pattern[str]] = re.compile(
    r'<div[^>]*class="claim-text"[^>]*>', re.IGNORECASE
)
_NUMBERED_CLAIM_RE: Final[re.Pattern[str]] = re.compile(r"(?m)^\s*(\d{1,3})\s*\.\s+")
_DATE_KEYS: Final[dict[str, str]] = {
    "publication": "publicationDate",
    "priority": "priorityDate",
    "filing": "filingDate",
}


def fetch_patent(
    number: str,
    *,
    fetch: Fetch | None = None,
    timeout: float = 20.0,
) -> PatentRecord:
    """Fetch and extract one publication. Missing sections come back empty, not raised."""
    clean_number = re.sub(r"[^A-Za-z0-9]", "", number or "")
    if not clean_number:
        raise SearchUnavailable(f"not a usable publication number: {number!r}")
    url = PATENT_URL.format(number=clean_number)
    fetcher = fetch or _default_fetch(timeout)
    raw = fetcher(url, PAGE_HEADERS)
    if not isinstance(raw, bytes | bytearray):
        raise SearchUnavailable(f"fetch() returned {type(raw).__name__}, expected bytes")
    body = bytes(raw)
    html_text = body.decode("utf-8", errors="replace")
    return PatentRecord(
        number=clean_number,
        title=_extract_title(html_text, clean_number),
        abstract=_extract_abstract(html_text),
        claims=_extract_claims(html_text),
        description_excerpt=_extract_description(html_text),
        cpc=_extract_cpc(html_text),
        dates=_extract_dates(html_text),
        url=url,
        retrieved_at=_now(),
        sha256=_sha256_bytes(body),
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean(raw: str) -> str:
    """Strip tags, unescape entities, collapse whitespace. Safe on any fragment."""
    text = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_block(raw: str) -> str:
    """Like `_clean` but keeps paragraph breaks, for description excerpts."""
    text = _SCRIPT_RE.sub(" ", raw)
    text = re.sub(r"(?i)</(p|div|li|h\d|section)>", "\n\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()


def _matched_block(html_text: str, open_match: re.Match[str], tag: str) -> tuple[str, int]:
    """Inner HTML of a possibly nested element, plus the offset just past its close.

    Google Patents nests `div.claim-text` inside `div.claim-text`, so a
    non-greedy `.*?</div>` truncates a claim at its first sub-clause. Counting
    opens and closes is cruder than a parser and entirely adequate here.
    """
    open_re = re.compile(rf"<{tag}\b", re.IGNORECASE)
    close_re = re.compile(rf"</{tag}\s*>", re.IGNORECASE)
    depth = 1
    position = open_match.end()
    while depth > 0:
        next_open = open_re.search(html_text, position)
        next_close = close_re.search(html_text, position)
        if next_close is None:
            return html_text[open_match.end() :], len(html_text)
        if next_open is not None and next_open.start() < next_close.start():
            depth += 1
            position = next_open.end()
            continue
        depth -= 1
        position = next_close.end()
        if depth == 0:
            return html_text[open_match.end() : next_close.start()], position
    return "", position


def _section(html_text: str, itemprop: str) -> str:
    """Inner HTML of `<section itemprop="...">`, or `""` when the page lacks it."""
    match = re.search(rf'<section[^>]*itemprop="{itemprop}"[^>]*>', html_text, re.IGNORECASE)
    if match is None:
        return ""
    inner, _ = _matched_block(html_text, match, "section")
    return re.sub(r"(?is)<h2\b.*?</h2>", " ", inner)


def _extract_title(html_text: str, number: str) -> str:
    match = _META_TITLE_RE.search(html_text)
    if match:
        title = _clean(match.group(1))
        if title:
            return title
    match = _TITLE_TAG_RE.search(html_text)
    if match:
        title = _clean(match.group(1))
        title = re.sub(r"\s*-\s*Google Patents\s*$", "", title)
        title = re.sub(rf"^{re.escape(number)}\s*-\s*", "", title)
        if title:
            return title
    return ""


def _extract_abstract(html_text: str) -> str:
    section = _section(html_text, "abstract")
    if section.strip():
        text = _clean(section)
        if text:
            return text
    match = _ABSTRACT_DIV_RE.search(html_text)
    if match:
        inner, _ = _matched_block(html_text, match, "div")
        text = _clean(inner)
        if text:
            return text
    match = _META_DESCRIPTION_RE.search(html_text)
    if match:
        return _clean(match.group(1))
    return ""


def _extract_claims(html_text: str) -> list[str]:
    section = _section(html_text, "claims")
    if not section.strip():
        return []
    claims = _blocks(section, _CLAIM_DIV_RE) or _blocks(section, _CLAIM_TEXT_DIV_RE)
    if claims:
        return claims
    # Last resort: one flat string with claim numbers in it.
    flat = _clean(section)
    starts = [m.start() for m in _NUMBERED_CLAIM_RE.finditer(flat)]
    if not starts:
        return [flat] if flat else []
    bounds = [*starts, len(flat)]
    out = [flat[bounds[i] : bounds[i + 1]].strip() for i in range(len(starts))]
    return [claim for claim in out if claim]


def _blocks(section: str, opener: re.Pattern[str]) -> list[str]:
    """Text of every *outermost* element matching `opener` within `section`."""
    out: list[str] = []
    position = 0
    while True:
        match = opener.search(section, position)
        if match is None:
            return out
        inner, position = _matched_block(section, match, "div")
        text = _clean(inner)
        if text:
            out.append(text)


def _extract_description(html_text: str) -> str:
    section = _section(html_text, "description")
    if not section.strip():
        return ""
    text = _clean_block(section)
    if len(text) <= DESCRIPTION_EXCERPT_CHARS:
        return text
    return text[:DESCRIPTION_EXCERPT_CHARS].rstrip()


def _extract_cpc(html_text: str) -> list[str]:
    """Full subgroup codes only: the `B`, `B62`, `B62D` parents add no information."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _CODE_RE.finditer(html_text):
        code = _clean(match.group(1))
        if "/" not in code or code in seen:
            continue
        seen.add(code)
        out.append(code)
        if len(out) >= MAX_CPC_CODES:
            break
    return out


def _extract_dates(html_text: str) -> dict[str, str]:
    dates: dict[str, str] = {}
    for key, itemprop in _DATE_KEYS.items():
        match = re.search(
            rf'itemprop="{itemprop}"[^>]*?datetime="([^"]*)"', html_text, re.IGNORECASE
        )
        if match is None:
            match = re.search(rf'itemprop="{itemprop}"[^>]*>([^<]*)<', html_text, re.IGNORECASE)
        dates[key] = _clean(match.group(1)) if match else ""
    return dates


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def run_search(
    dossier: Dossier,
    *,
    max_queries: int = 12,
    per_query: int = 10,
    max_candidates: int = 25,
    fetch: Fetch | None = None,
    sleep_s: float = 1.0,
    timeout: float = 20.0,
    retries: int = 1,
) -> SearchRun:
    """Run Phase 2's queries, fetch the best candidates, write them into intake.

    The service throttles bursts hard (a 503 after a dozen requests in a few
    minutes, in practice), so calls are serialised with `sleep_s` between them
    and a transient failure is retried `retries` times with a short backoff.
    Failure of any one query or page is recorded and stepped over. The run as a
    whole fails only when no query succeeded, because then the empty candidate
    set would otherwise read as "the art is clear".
    """
    fetcher = fetch or _default_fetch(timeout)
    attempts = max(retries, 0) + 1
    backoff_s = sleep_s * 3
    started_at = _now()
    errors: list[str] = []

    queries = queries_from_phase2(dossier, max_queries=max_queries)
    if not queries:
        raise NoQueries(
            "no search queries could be derived from Phase 2: run Prompt 06 first, or add "
            "`search_queries` to its envelope"
        )
    query_source = queries[0].source or None

    query_log: list[dict[str, Any]] = []
    hits_by_number: dict[str, Candidate] = {}
    succeeded = 0

    for index, query in enumerate(queries):
        if index and sleep_s > 0:
            time.sleep(sleep_s)
        url = build_query_url(query.text, per_query)
        entry: dict[str, Any] = {
            "query": query.text,
            "purpose": query.purpose,
            "source": query.source,
            "url": url,
            "sent_at": _now(),
            "total_results": 0,
            "returned": 0,
            "hits": [],
            "error": None,
        }
        try:
            payload = json.loads(
                _attempt(partial(_fetch_text, fetcher, url, QUERY_HEADERS), attempts, backoff_s)
            )
            if not isinstance(payload, dict):
                raise SearchUnavailable("query reply was not a JSON object")
            total, hits = _parse_query_payload(payload, query=query.text, num=per_query)
        except Exception as exc:  # noqa: BLE001 - one bad query must not end the run
            message = f"query {query.text!r} failed: {_describe(exc)}"
            log.warning("%s", message)
            entry["error"] = _describe(exc)
            errors.append(message)
            query_log.append(entry)
            continue

        succeeded += 1
        entry["total_results"] = total
        entry["returned"] = len(hits)
        entry["hits"] = [
            {"number": h.number, "rank": h.rank, "title": h.title, "date": h.date} for h in hits
        ]
        query_log.append(entry)
        _merge(hits_by_number, hits)

    if succeeded == 0:
        raise SearchUnavailable(
            f"every query to {SERVICE_HOST} failed ({len(queries)} attempted); "
            f"first error: {errors[0] if errors else 'unknown'}"
        )

    candidates = _ranked(hits_by_number.values())
    selected = candidates[:max_candidates]

    records: list[PatentRecord] = []
    candidate_log: list[dict[str, Any]] = []
    written: list[Path] = []
    prior_art_dir = dossier.intake_dir / "prior-art"
    prior_art_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_machine_files(prior_art_dir)

    for position, candidate in enumerate(selected, start=1):
        if sleep_s > 0:
            time.sleep(sleep_s)
        record_entry: dict[str, Any] = {
            "number": candidate.number,
            "hit_count": candidate.hit_count,
            "rank": candidate.rank,
            "queries": list(candidate.queries),
            "url": candidate.url,
            "fetched": False,
            "file": None,
            "sha256": None,
            "retrieved_at": None,
            "error": None,
        }
        try:
            record = _attempt(
                partial(fetch_patent, candidate.number, fetch=fetcher, timeout=timeout),
                attempts,
                backoff_s,
            )
        except Exception as exc:  # noqa: BLE001 - a dead page must not end the run
            message = f"fetching {candidate.number} failed: {_describe(exc)}"
            log.warning("%s", message)
            record_entry["error"] = _describe(exc)
            errors.append(message)
            candidate_log.append(record_entry)
            continue

        path = prior_art_dir / _candidate_filename(position, candidate.number)
        dossier.write_file(path, _render_candidate(candidate, record, position), overwrite=True)
        records.append(record)
        written.append(path)
        record_entry.update(
            {
                "fetched": True,
                "file": path.name,
                "sha256": record.sha256,
                "retrieved_at": record.retrieved_at,
                "title": record.title or candidate.title,
            }
        )
        candidate_log.append(record_entry)

    finished_at = _now()
    counts = {
        "queries": len(queries),
        "queries_failed": len(queries) - succeeded,
        "hits": sum(entry["returned"] for entry in query_log),
        "unique_candidates": len(candidates),
        "fetched": len(records),
        "files": len(written),
        "errors": len(errors),
    }

    log_payload = {
        "tool": "ipfe search",
        "service": SERVICE_NAME,
        "matter": dossier.slug,
        "started_at": started_at,
        "finished_at": finished_at,
        "query_source": query_source,
        "settings": {
            "max_queries": max_queries,
            "per_query": per_query,
            "max_candidates": max_candidates,
        },
        "counts": counts,
        "queries": query_log,
        "candidates": candidate_log,
        "errors": errors,
    }
    log_path = prior_art_dir / LOG_NAME
    dossier.write_file(
        log_path, json.dumps(log_payload, indent=2, ensure_ascii=False) + "\n", overwrite=True
    )
    readme_path = prior_art_dir / README_NAME
    dossier.write_file(
        readme_path,
        _render_readme(dossier.slug, started_at, finished_at, queries, counts, written, errors),
        overwrite=True,
    )

    log.info(
        "search: %d queries (%d failed), %d unique candidates, %d files written to %s",
        counts["queries"],
        counts["queries_failed"],
        counts["unique_candidates"],
        counts["files"],
        prior_art_dir,
    )
    return SearchRun(
        matter=dossier.slug,
        started_at=started_at,
        finished_at=finished_at,
        queries=queries,
        query_source=query_source,
        candidates=candidates,
        records=records,
        files=written,
        log_path=log_path,
        readme_path=readme_path,
        errors=errors,
        counts=counts,
    )


_TRANSIENT_STATUS: Final[frozenset[int]] = frozenset({408, 429, 500, 502, 503, 504})


def _transient(exc: BaseException) -> bool:
    """Worth one more attempt: a throttle, a gateway blip, a dropped connection."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_STATUS
    return isinstance(exc, urllib.error.URLError | TimeoutError)


def _attempt[T](call: Callable[[], T], attempts: int, backoff_s: float) -> T:
    """Call, retrying only transient failures. Anything else raises immediately."""
    last: BaseException | None = None
    for number in range(max(attempts, 1)):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - re-raised below once we give up
            last = exc
            if number == attempts - 1 or not _transient(exc):
                raise
            log.info("transient failure (%s); retrying in %.1fs", _describe(exc), backoff_s)
            if backoff_s > 0:
                time.sleep(backoff_s)
    raise last if last is not None else SearchUnavailable("no attempt was made")


def _describe(exc: BaseException) -> str:
    """One line, no stack, no page body - this string is written to the dossier."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        return f"network error: {exc.reason}"
    return f"{type(exc).__name__}: {exc}"


def _merge(into: dict[str, Candidate], hits: Iterable[Hit]) -> None:
    """Fold one query's hits into the candidate set, keeping the best rank seen."""
    for hit in hits:
        existing = into.get(hit.number)
        if existing is None:
            into[hit.number] = Candidate(
                number=hit.number,
                title=hit.title,
                date=hit.date,
                assignee=hit.assignee,
                snippet=hit.snippet,
                url=hit.url,
                rank=hit.rank,
                hit_count=1,
                queries=[hit.query],
            )
            continue
        if hit.query not in existing.queries:
            existing.queries.append(hit.query)
            existing.hit_count += 1
        existing.rank = min(existing.rank, hit.rank)
        existing.title = existing.title or hit.title
        existing.date = existing.date or hit.date
        existing.assignee = existing.assignee or hit.assignee
        existing.snippet = existing.snippet or hit.snippet


def _ranked(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Most queries first, then best rank, then newest. Sorts are stable, so chain them."""
    out = list(candidates)
    out.sort(key=lambda c: c.date, reverse=True)
    out.sort(key=lambda c: (-c.hit_count, c.rank))
    return out


def _candidate_filename(position: int, number: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]", "", number) or "unknown"
    return f"{MACHINE_PREFIX}{position:02d}-{safe}.md"


def _remove_stale_machine_files(prior_art_dir: Path) -> None:
    """Clear the previous machine set only. Human files do not match the glob."""
    for path in sorted(prior_art_dir.glob(f"{MACHINE_PREFIX}*.md")):
        if path.is_file():
            path.unlink()


def _render_candidate(candidate: Candidate, record: PatentRecord, position: int) -> str:
    title = record.title or candidate.title or "(title not extracted)"
    queries = "; ".join(candidate.queries)
    dates = record.dates
    lines = [
        f"<!-- MACHINE-RETRIEVED prior art candidate: not verified by a human; "
        f"retrieved {record.retrieved_at} from {record.url} by ipfe search; "
        f"matched queries: {queries} -->",
        "",
        f"# {record.number} — {title}",
        "",
        "> Machine-retrieved from Google Patents by `ipfe search`; **not verified by a human**.",
        "> The text below is an automated extract of the public patent page and may be",
        "> incomplete or mis-segmented. Chart only what is quoted here, and flag anything",
        "> that looks truncated.",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Publication number | {record.number} |",
        f"| Publication date | {dates.get('publication') or candidate.date or '(unknown)'} |",
        f"| Priority date | {dates.get('priority') or '(unknown)'} |",
        f"| Filing date | {dates.get('filing') or '(unknown)'} |",
        f"| Assignee (as indexed) | {candidate.assignee or '(unknown)'} |",
        f"| CPC | {'; '.join(record.cpc) if record.cpc else '(none extracted)'} |",
        f"| Source | {record.url} |",
        f"| Retrieved | {record.retrieved_at} |",
        f"| SHA-256 of source page | {record.sha256} |",
        f"| Matched queries | {candidate.hit_count} |",
        f"| Best result rank | {candidate.rank} |",
        f"| Queries | {queries} |",
        "",
        "## Abstract",
        "",
        record.abstract or "(no abstract extracted from the page)",
        "",
        "## Claims",
        "",
    ]
    if record.claims:
        for index, claim in enumerate(record.claims, start=1):
            lines.append(f"{index}. {claim}")
            lines.append("")
    else:
        lines.extend(["(no claims extracted from the page)", ""])
    lines.extend(
        [
            "## Description excerpt",
            "",
            record.description_excerpt or "(no description extracted from the page)",
            "",
            f"*Excerpt only: the first {DESCRIPTION_EXCERPT_CHARS} characters of the description "
            "as published on the source page. The full document must be read before any "
            "reliance is placed on what is absent from it.*",
            "",
        ]
    )
    return "\n".join(lines)


def _render_readme(
    slug: str,
    started_at: str,
    finished_at: str,
    queries: list[Query],
    counts: dict[str, int],
    written: list[Path],
    errors: list[str],
) -> str:
    query_lines = "\n".join(f"{i}. `{q.text}`" for i, q in enumerate(queries, start=1))
    file_lines = "\n".join(f"- `{p.name}`" for p in written) or "- (none)"
    error_lines = "\n".join(f"- {e}" for e in errors) or "- (none)"
    return f"""<!-- Written by `ipfe search`; rewritten on every search run. -->

# Machine-retrieved prior-art candidates — {slug}

A prior-art search was run for this matter because it was explicitly requested
(`ipfe run --search`). This file records what happened, so that the disclosure to a
third party is on the record in the dossier itself.

## What left this machine

The {counts["queries"]} search strings listed below, and nothing else. They were sent to
**{SERVICE_NAME}**, a third-party service outside your control, between {started_at}
and {finished_at}. The disclosure, the Phase 1 analysis, the inventive nucleus, the
terminology table and the claims were **not** transmitted and are never read by the search
code. The strings themselves come from the Prompt 06 search-concept decomposition, which
means they describe the technical field of the invention: treat that as a disclosure of
subject-matter interest to whoever can observe the queries.

{query_lines}

## What came back

- Queries run: {counts["queries"]} ({counts["queries_failed"]} failed)
- Result rows returned: {counts["hits"]}
- Unique publications: {counts["unique_candidates"]}
- Pages fetched and written: {counts["files"]}

Files written (each replaces any file of the same name from an earlier search run):

{file_lines}

Errors (each query or page that failed was skipped, not retried indefinitely):

{error_lines}

`{LOG_NAME}` holds the full record: every query with its URL, timestamp, result count and
hit list, and every fetched page with its sha256 and timestamp.

## How to read these files

Each `{MACHINE_PREFIX}*.md` file starts with a `MACHINE-RETRIEVED` marker. It means:

- **Nobody has verified it.** It was ranked by how many of the queries surfaced it, not
  by relevance to the inventive nucleus.
- **It is an extract, not the document.** Abstract, claims and the first
  {DESCRIPTION_EXCERPT_CHARS} characters of the description, scraped from the public page.
  Anything read as *absent* from it may simply be outside the excerpt.
- **It may be irrelevant.** Automated retrieval has no judgement; triage does.

Files that do **not** start with `{MACHINE_PREFIX}` were supplied by a human and are never
touched, moved or overwritten by the search.

## What happens next

`ip-searcher` triages every candidate (relevance, which claim concepts it touches, the most
relevant passage) and shortlists at most eight for charting; Prompt 07 then charts the
shortlist element by element, exactly as it charts human-supplied references.

## This is not a professional search

An automated keyword search of one database is not a patentability or freedom-to-operate
search. It misses non-English and non-patent literature, it misses anything the queries did
not phrase well, and it cannot read around a claim. A professional search by a qualified
searcher remains required before filing.
"""
