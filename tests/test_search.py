"""Prior-art search: query extraction, retrieval, merging, and what lands in intake.

Every test drives `ecosystem.search` through a fake `fetch`, so the suite never
opens a socket. The fixtures under `tests/fixtures/search/` are hand-written but
shaped like the real Google Patents replies: HTML tags inside result titles,
nested `div.claim-text`, classification codes as `<span itemprop="Code">`, and
one page that is missing its abstract section entirely.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from ecosystem.dossier import Dossier
from ecosystem.search import (
    QUERY_ENDPOINT,
    NoQueries,
    SearchUnavailable,
    build_query_url,
    fetch_patent,
    normalise_query,
    queries_from_markdown,
    queries_from_phase2,
    run_search,
    search,
    wire_query,
)
from tests.conftest import write_version

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "search"

VALVE_QUERY = '"valve core" "tyre sealant"'
SEAT_QUERY = '"compliant seat" OR "elastomer seat"'


# ---------------------------------------------------------------------------
# fake transport
# ---------------------------------------------------------------------------


def query_text_of(url: str) -> str:
    """Undo the endpoint's double encoding: `?url=q%3D...%26num%3D10`."""
    inner = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["url"][0]
    return urllib.parse.parse_qs(inner)["q"][0]


class FakeFetch:
    """`fetch(url, headers) -> bytes` backed by fixture files, with injectable faults."""

    def __init__(
        self,
        queries: dict[str, str] | None = None,
        pages: dict[str, str] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.queries = queries or {}
        self.pages = pages or {}
        self.errors = errors or {}
        self.calls: list[str] = []
        self.sent_queries: list[str] = []

    def __call__(self, url: str, headers: dict[str, str]) -> bytes:
        assert headers.get("User-Agent"), "every request must identify a user agent"
        self.calls.append(url)
        if url.startswith(QUERY_ENDPOINT):
            key = query_text_of(url)
            self.sent_queries.append(key)
            table = self.queries
        else:
            key = url.rsplit("/patent/", 1)[1].split("/")[0]
            table = self.pages
        if key in self.errors:
            raise self.errors[key]
        if key not in table:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        return (FIXTURES / table[key]).read_bytes()


def full_fetch(**overrides: Any) -> FakeFetch:
    return FakeFetch(
        queries={VALVE_QUERY: "query-valve.json", SEAT_QUERY: "query-seat.json"},
        pages={
            "US1234567B2": "patent-US1234567B2.html",
            "EP3456789B1": "patent-EP3456789B1.html",
        },
        **overrides,
    )


ENVELOPE_06: dict[str, Any] = {
    "prompt": "06",
    "status": "complete",
    "search_queries": [
        {"query": VALVE_QUERY, "purpose": "core architecture"},
        {"query": SEAT_QUERY, "purpose": "functional equivalents"},
    ],
}


def seed_phase2(dossier: Dossier, envelope: dict[str, Any] | None = None, body: str = "") -> None:
    """One complete P2 version carrying a Prompt 06 output (+ optional envelope)."""
    write_version(
        dossier,
        "P2",
        {"06": body or "## Deliverable\n\nNo search strings here.\n"},
        envelopes={"06": envelope} if envelope else None,
    )


# ---------------------------------------------------------------------------
# query extraction
# ---------------------------------------------------------------------------


def test_queries_come_from_the_06_envelope_when_it_has_them(dossier: Dossier) -> None:
    seed_phase2(dossier, ENVELOPE_06)

    queries = queries_from_phase2(dossier)

    assert [q.text for q in queries] == [VALVE_QUERY, SEAT_QUERY]
    assert queries[0].purpose == "core architecture"
    assert queries[0].source == "06.envelope.json"


def test_queries_are_parsed_from_the_deliverable_when_the_envelope_has_none(
    dossier: Dossier,
) -> None:
    body = """## Deliverable

### Search Concept Decomposition

#### Core architecture

Concept statement: a valve core that purges itself on inflation. This prose line mentions
a valve core but is not a search string and must not be sent.

Ready-to-run search strings:

```
"valve core" AND "tyre sealant"
("purge gap" OR "self-cleaning") AND valve
```

- CPC theme: `CPC=F16K 15/20`
- Applicant search: "Northbridge Cycle Components" AND valve
- Plain bullet with no operator and no quoted phrase

| Track | Search string | Note |
|---|---|---|
| Components | "superelastic seat" NEAR poppet | narrow |
| Duplicate | "valve core" AND "tyre sealant" | already listed above |
"""
    seed_phase2(dossier, envelope=None, body=body)

    queries = [q.text for q in queries_from_phase2(dossier)]

    assert queries == [
        '"valve core" "tyre sealant"',
        '("purge gap" OR "self-cleaning") valve',
        "cpc:F16K15/20",
        '"Northbridge Cycle Components" valve',
        '"superelastic seat" NEAR poppet',
    ]
    assert all(q.source.startswith("06-") for q in queries_from_phase2(dossier))


def test_query_extraction_caps_and_dedupes() -> None:
    markdown = "\n".join(f'- "term {n}" AND valve' for n in range(20)) + '\n- "term 0" AND valve\n'

    assert len(queries_from_markdown(markdown, max_queries=12)) == 12
    assert len(queries_from_markdown(markdown, max_queries=100)) == 20


def test_queries_from_phase2_is_empty_when_phase2_has_not_run(dossier: Dossier) -> None:
    assert queries_from_phase2(dossier) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"valve core" AND sealant', '"valve core" sealant'),
        ("- **Search string:** poppet AND seat", "poppet seat"),
        ("`CPC=F16K 15/20`", "cpc:F16K15/20"),
        ("CPC: F16K 15/20", "cpc:F16K15/20"),
        ('"a" OR "b" NEAR "c"', '"a" OR "b" NEAR "c"'),
        ("android sandbox", "android sandbox"),  # AND inside a word survives
        ("", ""),
        ("--", ""),
    ],
)
def test_normalise_query(raw: str, expected: str) -> None:
    assert normalise_query(raw) == expected


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------


def test_build_query_url_double_encodes_the_query() -> None:
    url = build_query_url('"valve core"', num=10)

    assert url.startswith(f"{QUERY_ENDPOINT}?url=")
    assert url.endswith("&exp=")
    assert query_text_of(url) == '"valve core"'


def test_classification_prefixes_are_stripped_on_the_wire() -> None:
    """`cpc:CODE` in `q=` returns nothing from the live endpoint; the bare code works."""
    assert wire_query("cpc:F16K15/20 sealant") == "F16K15/20 sealant"
    assert wire_query('"valve core" ipc:B60C29/06') == '"valve core" B60C29/06'
    assert wire_query('"valve core"') == '"valve core"'
    assert query_text_of(build_query_url("cpc:F16K15/20")) == "F16K15/20"


def test_search_returns_ranked_hits_with_tags_stripped_from_titles() -> None:
    fetch = full_fetch()

    hits = search(VALVE_QUERY, num=10, fetch=fetch)

    assert [h.number for h in hits] == ["US1234567B2", "EP3456789B1", "US2019012345A1"]
    assert [h.rank for h in hits] == [1, 2, 3]
    assert hits[0].title == "Self-purging valve core with compliant seat"
    assert "<b>" not in hits[0].snippet and "…" in hits[0].snippet
    assert hits[0].url == "https://patents.google.com/patent/US1234567B2/en"
    assert hits[0].date == "2019-06-11"
    assert hits[0].query == VALVE_QUERY


def test_search_respects_num() -> None:
    assert len(search(VALVE_QUERY, num=2, fetch=full_fetch())) == 2


def test_search_raises_on_a_non_json_reply() -> None:
    fetch = FakeFetch(queries={VALVE_QUERY: "patent-US1234567B2.html"})

    with pytest.raises(SearchUnavailable, match="non-JSON"):
        search(VALVE_QUERY, fetch=fetch)


def test_fetch_patent_extracts_abstract_claims_cpc_and_dates() -> None:
    record = fetch_patent("US1234567B2", fetch=full_fetch())

    assert record.title == "Self-purging valve core with compliant seat"
    assert record.abstract.startswith("A valve core for a pneumatic tyre valve comprises")
    assert "cleared from the sealing face" in record.abstract  # entity unescaped, tags gone
    assert "&amp;" not in record.abstract
    assert len(record.claims) == 3
    # The nested claim-text divs of claim 1 must arrive as one claim, not four.
    assert record.claims[0].startswith("1. A valve core comprising:")
    assert "the seat being compliant" in record.claims[0]
    assert record.cpc == ["F16K15/20", "B60C29/06"]  # parents dropped, duplicate deduped
    assert record.dates == {
        "publication": "2019-06-11",
        "priority": "2016-03-02",
        "filing": "2017-03-01",
    }
    assert "FIELD OF THE INVENTION" in record.description_excerpt
    assert len(record.description_excerpt) <= 3000
    assert len(record.sha256) == 64
    assert record.url == "https://patents.google.com/patent/US1234567B2/en"


def test_fetch_patent_tolerates_a_page_without_an_abstract_section() -> None:
    record = fetch_patent("EP3456789B1", fetch=full_fetch())

    # No <section itemprop="abstract">: the meta description is the fallback.
    assert record.abstract.startswith("A tyre valve in which the seat is an elastomeric ring")
    assert record.title == "Tyre valve with flexible elastomer seat"
    assert record.cpc == ["F16K15/14"]
    assert record.dates["filing"] == ""
    assert len(record.claims) == 2


def test_fetch_patent_rejects_a_junk_publication_number() -> None:
    with pytest.raises(SearchUnavailable, match="usable publication number"):
        fetch_patent("///", fetch=full_fetch())


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def run(dossier: Dossier, fetch: FakeFetch, **kwargs: Any) -> Any:
    return run_search(dossier, fetch=fetch, sleep_s=0, **kwargs)


def test_run_search_merges_hits_and_ranks_by_overlap(dossier: Dossier) -> None:
    seed_phase2(dossier, ENVELOPE_06)

    result = run(dossier, full_fetch())

    # EP3456789B1 is hit by both queries, so it outranks the query-1 top hit.
    assert [c.number for c in result.candidates] == [
        "EP3456789B1",
        "US1234567B2",
        "US9999999B1",
        "US2019012345A1",
    ]
    top = result.candidates[0]
    assert top.hit_count == 2
    assert top.queries == [VALVE_QUERY, SEAT_QUERY]
    assert top.rank == 1  # best rank across queries
    assert result.counts["unique_candidates"] == 4
    assert result.counts["queries"] == 2


def test_run_search_writes_marked_candidate_files(dossier: Dossier) -> None:
    seed_phase2(dossier, ENVELOPE_06)

    result = run(dossier, full_fetch())

    names = [p.name for p in result.files]
    assert names == ["machine-01-EP3456789B1.md", "machine-02-US1234567B2.md"]
    text = result.files[0].read_text(encoding="utf-8")
    first_line = text.splitlines()[0]
    assert first_line.startswith(
        "<!-- MACHINE-RETRIEVED prior art candidate: not verified by a human;"
    )
    assert "https://patents.google.com/patent/EP3456789B1/en" in first_line
    assert "by ipfe search" in first_line
    assert f"matched queries: {VALVE_QUERY}; {SEAT_QUERY}" in first_line
    assert "# EP3456789B1 — Tyre valve with flexible elastomer seat" in text
    assert "| Priority date | 2018-06-09 |" in text
    assert "| CPC | F16K15/14 |" in text
    assert "## Abstract" in text
    assert "## Claims" in text
    assert "1. A tyre valve comprising a body" in text
    assert "## Description excerpt" in text


def test_run_search_writes_the_log_and_the_readme(dossier: Dossier) -> None:
    seed_phase2(dossier, ENVELOPE_06)

    result = run(dossier, full_fetch())
    payload = json.loads(result.log_path.read_text(encoding="utf-8"))

    assert result.log_path.name == "SEARCH-LOG.json"
    assert payload["service"].startswith("Google Patents")
    assert payload["matter"] == "demo"
    assert [entry["query"] for entry in payload["queries"]] == [VALVE_QUERY, SEAT_QUERY]
    assert payload["queries"][0]["total_results"] == 1423
    assert payload["queries"][0]["returned"] == 3
    assert payload["queries"][0]["hits"][0]["number"] == "US1234567B2"
    assert payload["queries"][0]["sent_at"]
    fetched = [c for c in payload["candidates"] if c["fetched"]]
    assert [c["number"] for c in fetched] == ["EP3456789B1", "US1234567B2"]
    assert all(len(c["sha256"]) == 64 and c["retrieved_at"] for c in fetched)
    assert payload["counts"]["files"] == 2

    readme = result.readme_path.read_text(encoding="utf-8")
    assert result.readme_path.name == "SEARCH-README.md"
    assert "Google Patents" in readme
    assert VALVE_QUERY in readme
    assert "professional search" in readme
    assert "machine-01-EP3456789B1.md" in readme


def test_run_search_never_touches_a_human_supplied_file(dossier: Dossier) -> None:
    seed_phase2(dossier, ENVELOPE_06)
    human = dossier.intake_dir / "prior-art" / "ref-01-human.md"
    human.write_text("# Human reference\n", encoding="utf-8")
    stale = dossier.intake_dir / "prior-art" / "machine-09-OLD123A1.md"
    stale.write_text("# stale machine candidate from a previous run\n", encoding="utf-8")

    run(dossier, full_fetch())

    assert human.read_text(encoding="utf-8") == "# Human reference\n"
    assert not stale.exists()  # the previous machine set is replaced, not accumulated
    assert sorted(p.name for p in (dossier.intake_dir / "prior-art").iterdir()) == [
        "SEARCH-LOG.json",
        "SEARCH-README.md",
        "machine-01-EP3456789B1.md",
        "machine-02-US1234567B2.md",
        "ref-01-human.md",
    ]


def test_run_search_is_idempotent_across_reruns(dossier: Dossier) -> None:
    seed_phase2(dossier, ENVELOPE_06)

    first = run(dossier, full_fetch())
    second = run(dossier, full_fetch())

    assert [p.name for p in first.files] == [p.name for p in second.files]


def test_run_search_respects_max_candidates(dossier: Dossier) -> None:
    seed_phase2(dossier, ENVELOPE_06)

    result = run(dossier, full_fetch(), max_candidates=1)

    assert [p.name for p in result.files] == ["machine-01-EP3456789B1.md"]
    assert result.counts["unique_candidates"] == 4


def test_run_search_survives_a_failed_query_and_a_failed_page(dossier: Dossier) -> None:
    seed_phase2(dossier, ENVELOPE_06)
    fetch = full_fetch(
        errors={
            SEAT_QUERY: urllib.error.HTTPError(
                "https://patents.google.com",
                429,
                "Too Many Requests",
                None,
                None,  # type: ignore[arg-type]
            ),
            "US1234567B2": urllib.error.URLError("connection reset"),
        }
    )

    result = run(dossier, fetch)

    assert result.counts["queries_failed"] == 1
    # Query 1 still produced candidates; only the page that failed is missing, and the
    # file number keeps the rank position so the gap is visible rather than silent.
    assert [p.name for p in result.files] == ["machine-02-EP3456789B1.md"]
    assert any("HTTP 429" in e for e in result.errors)
    assert any("connection reset" in e for e in result.errors)

    payload = json.loads(result.log_path.read_text(encoding="utf-8"))
    failed_query = next(q for q in payload["queries"] if q["query"] == SEAT_QUERY)
    assert failed_query["error"] == "HTTP 429 Too Many Requests"
    assert failed_query["hits"] == []
    failed_page = next(c for c in payload["candidates"] if c["number"] == "US1234567B2")
    assert failed_page["fetched"] is False
    assert "connection reset" in failed_page["error"]


def test_run_search_retries_a_throttled_query_once(dossier: Dossier) -> None:
    seed_phase2(dossier, ENVELOPE_06)
    inner = full_fetch()
    state = {"thrown": 0}

    def flaky(url: str, headers: dict[str, str]) -> bytes:
        if "xhr/query" in url and state["thrown"] == 0:
            state["thrown"] = 1
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)  # type: ignore[arg-type]
        return inner(url, headers)

    result = run_search(dossier, fetch=flaky, sleep_s=0)

    assert state["thrown"] == 1
    assert result.counts["queries_failed"] == 0  # the retry succeeded
    assert not [e for e in result.errors if "query" in e]


def test_run_search_does_not_retry_a_404(dossier: Dossier) -> None:
    """A missing page is permanent; retrying it just doubles the load on the service."""
    seed_phase2(dossier, ENVELOPE_06)
    inner = full_fetch()
    seen: list[str] = []

    def counting(url: str, headers: dict[str, str]) -> bytes:
        seen.append(url)
        return inner(url, headers)

    run_search(dossier, fetch=counting, sleep_s=0)

    missing = [u for u in seen if u.endswith("/patent/US9999999B1/en")]
    assert len(missing) == 1


def test_run_search_raises_when_every_query_fails(dossier: Dossier) -> None:
    seed_phase2(dossier, ENVELOPE_06)
    boom = urllib.error.HTTPError(
        "https://patents.google.com",
        429,
        "Too Many Requests",
        None,
        None,  # type: ignore[arg-type]
    )
    fetch = full_fetch(errors={VALVE_QUERY: boom, SEAT_QUERY: boom})

    with pytest.raises(SearchUnavailable, match="every query"):
        run(dossier, fetch)


def test_run_search_raises_when_phase2_produced_no_queries(dossier: Dossier) -> None:
    seed_phase2(dossier, envelope=None, body="## Deliverable\n\nNothing searchable here.\n")

    with pytest.raises(NoQueries, match="Prompt 06"):
        run(dossier, full_fetch())

    assert not (dossier.intake_dir / "prior-art" / "SEARCH-LOG.json").exists()


def test_run_search_sends_only_query_strings(dossier: Dossier) -> None:
    """The disclosure must never appear in a URL. Guards the privacy promise."""
    seed_phase2(dossier, ENVELOPE_06)
    disclosure = dossier.intake_dir / "disclosure.md"
    disclosure.write_text("SECRET-NUCLEUS-PHRASE that must not leave the machine\n", "utf-8")
    fetch = full_fetch()

    run(dossier, fetch)

    assert fetch.sent_queries == [VALVE_QUERY, SEAT_QUERY]
    assert all("SECRET-NUCLEUS-PHRASE" not in url for url in fetch.calls)


def test_espacenet_style_fenced_blocks_become_google_queries() -> None:
    from ecosystem.search import queries_from_markdown

    md = (
        '**Search strings.**\n\n```\nS-A1  ctxt=("valve core" or "valve insert")\n'
        '      and ctxt=("sealant" or "tyre sealant")\n```\n```\n'
        'S-A2  cpc=B60C29/low and ctxt=("clog*" or "deposit*")\n```\n\n'
        "| Theme | Codes | Confidence | Note |\n|---|---|---|---|\n"
        "| Seats | F16K 25/00, F16K 1/42 | `[medium]` | "
        'Seat construction; confirm which group carries "resilient seat ring" |\n'
        "| Two-stage | F16K 31/00 subgroups; F16K 1/44 | `[low]` | Guess — verify in the scheme |\n"
        "- Terms (OR within the family)\n"
    )
    queries = queries_from_markdown(md)
    assert queries[0] == '("valve core" OR "valve insert") ("sealant" OR "tyre sealant")'
    assert queries[1] == 'cpc:B60C29 ("clog*" OR "deposit*")'
    assert any("F16K" in q and "25/00" in q for q in queries)
    assert not any(
        "confirm" in q or "Guess" in q or "Terms (OR" in q or "subgroups" in q for q in queries
    )
