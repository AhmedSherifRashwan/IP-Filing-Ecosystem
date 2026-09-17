# Opt-in prior-art search

The ecosystem drafts from references a human supplies. `ipfe run --search` adds
one optional step to Phase 2: the runner sends the search strings Prompt 06
wrote to Google Patents, retrieves the best-matching publications, and writes
them into `00-intake/prior-art/` as clearly marked, unverified candidates for
the same charting the human-supplied references get.

It is off by default, it is never implied by any other flag, and nothing else in
the pipeline changes when it is off.

> An automated keyword search of one database is not a patentability or
> freedom-to-operate search. It does not replace a professional search by a
> qualified searcher before filing. See [What this is not](#what-this-is-not).

## Opting in

```bash
uv run ipfe run --matter demo --search
```

The flag is a per-run decision by the person driving the run, because it is the
only point in the pipeline where anything about the matter leaves the machine.
Consent is not remembered: a run without `--search` performs no retrieval, even
if a previous run of the same matter did. The opt-in and what it disclosed are
recorded in the dossier itself, in `00-intake/prior-art/SEARCH-README.md` and
`SEARCH-LOG.json`, so the record survives the shell history.

Everything else about the ecosystem is unchanged: the six drafting and review
agents still run `tools.profile: minimal` and still have no network, no browser
and no file access. The search is performed by the Python runner
(`ecosystem/search.py`, stdlib `urllib` only), not by an agent.

## What leaves the machine

**Query strings only.** Specifically, the search strings from the Prompt 06
search-concept decomposition: either the `search_queries` entries of its
envelope, or, if it produced none, the strings parsed out of its deliverable
(fenced blocks, bullets and table cells that quote a phrase, use `AND`/`OR`/`NEAR`,
or name a CPC/IPC class). At most 12 by default, normalised to Google Patents
syntax and deduped: quotes are kept, `AND` becomes a space (it is implicit),
`OR` and `NEAR` survive, and `CPC=F16K 15/20` becomes `cpc:F16K15/20`. The
`cpc:`/`ipc:` prefix is a note of intent for the reader, not wire syntax — it is
stripped on the way out, because the endpoint returns nothing for `cpc:CODE` in
a free-text query and the correct results for the bare code. The exact URL sent
is recorded beside every query in `SEARCH-LOG.json`.

**What never leaves the machine:** the disclosure, the Phase 1 analysis, the
inventive nucleus, the terminology table, the embodiments, the specification,
the claims, the review findings, the matter title, the slug, and any file under
`00-intake/`. `ecosystem/search.py` does not read them. The only outbound
requests are:

| Request | Carries |
|---|---|
| `GET patents.google.com/xhr/query?...` | one query string, and the number of results wanted |
| `GET patents.google.com/patent/<number>/en` | one publication number |

Understand what the queries themselves reveal. They describe the technical field,
the components and the functional effect of the invention, because that is what
makes them useful queries. Anyone able to observe them — Google, a network
operator, anyone with the logs — learns that someone is interested in that
subject matter, on that date. That is a disclosure decision, which is why it is a
flag and not a default. It is not a publication or an enabling disclosure of the
invention, but if the matter is sensitive enough that the search terms alone
matter, do not use the flag: commission a search under a professional engagement
instead.

## What is written, and where

Everything lands in `dossiers/<slug>/00-intake/prior-art/`, alongside the
human-supplied references:

| File | What it is |
|---|---|
| `machine-<NN>-<number>.md` | one candidate publication: metadata, abstract, claims, first 3000 characters of the description |
| `SEARCH-LOG.json` | every query with its URL, timestamp, result count and hit list; every fetched page with its sha256 and timestamp; every error |
| `SEARCH-README.md` | the human-readable record: what was sent, when, to whom, what came back |

Ranking is by how many of the queries surfaced a publication (`hit_count`),
then by its best result rank, then by date. The top 25 by default are fetched
and written; `<NN>` is the rank position, so a gap in the numbering means that
candidate's page could not be fetched — the reason is in `SEARCH-LOG.json`.

**Human files are never touched.** Only names beginning `machine-` are written,
and only those are deleted on a re-run, which replaces the previous machine set
rather than accumulating it. Anything else in the directory — whatever the
attorney put there, however it is named — is read-only to the search.

## The MACHINE-RETRIEVED marker

Every generated candidate file begins with:

```
<!-- MACHINE-RETRIEVED prior art candidate: not verified by a human; retrieved <ts> from <url> by ipfe search; matched queries: ... -->
```

It travels with the file into every prompt that embeds `intake:prior-art/*`, and
it means three things to every agent that reads it:

1. **Unverified.** It was ranked by query overlap, not by relevance to the
   inventive nucleus. It may be entirely off-target.
2. **An extract, not the document.** Abstract, claims and a truncated
   description scraped from a public page. Something absent from the file may
   simply be outside the excerpt, so silence is unknown, not absence — an
   important distinction when a chart marks a feature `absent`.
3. **Traceable.** The file carries its source URL, the retrieval timestamp, the
   sha256 of the page it was extracted from, and the queries that found it, so
   any assertion made from it can be checked against the real document.

## What Phase 2 does with the candidates

1. **Prompt 06** (`ip-prior-art`) decomposes the invention into search concepts
   and writes the search strings. Unchanged; it still does not run them.
2. **`ipfe search`** (the runner) executes the strings and writes the candidate
   files. No agent is involved.
3. **`x-search-triage`** (`ip-searcher`) reads every candidate, human-supplied
   and machine-retrieved alike, and produces a relevance verdict, the concepts
   each one touches, the single most relevant passage, and a shortlist of at
   most eight worth charting — plus the coverage gaps: which concepts nothing in
   the pile touches.
4. **Prompt 07** (`ip-prior-art`) charts the shortlist element by element,
   exactly as it charts human-supplied references, quoting passages and marking
   what is absent or ambiguous. Prompts 08–10 then run as usual.

The triage agent does not chart and does not judge novelty; the charting agent
does not triage. Keeping those apart is what stops a large machine-retrieved
pile from either swamping the charts or being quietly discarded.

## Failure behaviour

The search is best-effort and loud about it:

- A failed query (rate limit, HTTP error, unparseable reply) is logged, recorded
  in `SEARCH-LOG.json` with its error, and skipped. The other queries still run.
  A transient failure (429, 503, a dropped connection) is retried once after a
  short backoff; a 404 is not retried, because it will not change.
- A page that cannot be fetched or parsed is skipped the same way; the candidate
  keeps its entry in the log with `"fetched": false`.
- A page with a missing abstract, claims or classification section still yields a
  file, with the missing sections marked as not extracted.
- If **every** query fails, `run_search` raises `SearchUnavailable` and writes
  nothing, rather than leaving an empty candidate set that reads as "the art is
  clear".
- If Phase 2 produced no usable search strings, it raises `NoQueries`
  (a `SearchUnavailable`) telling the human to run Prompt 06 first.

Requests are made one at a time with a polite delay (1 s by default) and a
browser user agent, because the endpoint requires one. No API key, no
credentials, no account: nothing is authenticated and no secret is involved.

The endpoint throttles bursts: in testing, roughly a dozen requests within a few
minutes drew a sustained `503`, which then persisted for several minutes. A full
run at the defaults is up to 12 queries plus 25 page fetches, so expect some
requests to be refused and the candidate set to be smaller than
`max_candidates`. That is a degraded result, not a corrupted one: what was
retrieved is complete and hashed, and what was refused is named in
`SEARCH-LOG.json`. If a run comes back thin, raise `sleep_s`, cut
`max_queries`, and try again later rather than immediately.

Measured on one machine on 2026-09-17: about fifteen requests inside twenty
minutes produced a sustained `503` for the whole IP address — both the query
endpoint and plain patent pages, in a browser user agent as well as ours — and it
had not cleared five minutes later. A conservative first run (say six queries,
ten candidates, a two-second delay) is more likely to return something than a
full-width one that is refused halfway through.

## What this is not

An automated keyword search of one database, from strings composed by a language
model, retrieving at most a few dozen extracts, is a **starting point**. It
misses non-patent literature, non-English documents, anything phrased in
vocabulary the queries did not anticipate, and everything that requires reading
around a claim rather than matching a keyword. It has no judgement about
equivalence, and it cannot tell you that a field is clear — only that these
queries surfaced these documents on this day.

A professional search by a qualified searcher remains required before filing,
and freedom to operate is a separate question again (Prompt 34). Every agent
that touches these files is instructed to say so, and so is this document.
