---
id: "x-search-triage"
title: "Prior-art candidate triage"
phase: P2
agent: ip-searcher
kind: analysis
inputs: [out:P1/01, out:P1/03, out:P2/06, intake:prior-art/*]
optional_inputs: [terminology]
deliverable_sections: ["Candidate Triage", "Shortlist"]
timeout_s: 1200
include_rationale: false
---

*Runner template: sort the candidate references before anything is charted, so that Prompt 7 spends its effort on the references that can actually hurt.*

Act as a professional patent searcher triaging a candidate set. Every reference embedded under 'Dossier files' below is a candidate: some were supplied by a human, some were retrieved automatically from a public patent database and are marked `MACHINE-RETRIEVED`. Assess each one against the inventive nucleus and the search concepts, decide which are worth an element-by-element chart, and say where the candidate set does not cover the invention at all. You are not assessing novelty and you are not charting: you are deciding what deserves the chart.

**How to work.** Work only from the embedded candidates; never add, recall or allude to a reference that is not embedded, and never treat a `MACHINE-RETRIEVED` file as verified — it is an automated extract of a public page, it may be irrelevant, and its "Description excerpt" is truncated, so treat silence in it as unknown rather than as absence. Under `### Candidate Triage` give one H4 per candidate (the dossier file name, then the publication number and title) with: **Relevance** (`high` / `medium` / `low` / `irrelevant`) against the inventive nucleus from `03`; **Concepts touched** — which claim concepts or search tracks from `06` it reads on, using the locked terms; **Most relevant passage** — one quoted passage with its location in the file (claim number, paragraph, section heading), or "no relevant passage in the embedded text"; **Chart in Prompt 7?** — yes or no with a one-line reason; **Source** — human-supplied or machine-retrieved. Rank the H4s by relevance, highest first. At triage, recall beats precision: when you cannot tell from the embedded extract whether a reference matters, mark it `medium` and chart it rather than discarding it, and say which passage you would need to see to decide. Under `### Shortlist` give at most eight references as a table (publication number | file | relevance | why it is worth charting | which concept it attacks), ordered by how dangerous they look; if fewer than eight are worth charting, list fewer and say so. Then, still under `### Shortlist`, state plainly which concepts from `06` have no candidate at all, which have only machine-retrieved candidates, and that this candidate set is not a professional search and does not tell anyone that the art is clear. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.

**Envelope extras.** In addition to the fields the output contract names, include:

```json
{
  "shortlist": [{"number": "US1234567B2", "relevance": "high", "reason": "..."}],
  "coverage_gaps": ["concept or search track with no candidate against it"]
}
```

`shortlist` is the machine-readable form of `### Shortlist`, in the same order, at most eight entries, each `relevance` one of `high`, `medium`, `low`. `coverage_gaps` names the concepts nothing in the candidate set touches; an empty list means every concept has at least one candidate, never that coverage is adequate. Both lists must mirror the prose exactly.

**If no candidates are embedded below**, say so in one line, set `status` to `partial`, return an empty `shortlist`, list every concept from `06` in `coverage_gaps`, and add an open question that a professional search is required before Phase 3 proceeds.
