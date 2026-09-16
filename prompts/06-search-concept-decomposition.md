---
id: "06"
title: "Search concept decomposition"
phase: P2
agent: ip-prior-art
kind: analysis
inputs: [out:P1/01, out:P1/03, out:P1/04, terminology, intake:gate-1-answers]
optional_inputs: [intake:prior-art/*, out:P1/02]
deliverable_sections: ["Search Concept Decomposition"]
timeout_s: 1200
include_rationale: false
---

*Create a search strategy before relying on known references.*

Break the invention into searchable technical concepts and combinations. Generate keyword families, synonyms, CPC/IPC themes, applicant/assignee categories and search strings. Separate searches for the core architecture, individual components, functional equivalents, manufacturing method, use method and adjacent technologies.

**How to work.** The synonym and genus/species columns of the terminology table are your keyword seed; extend them for search purposes but do not treat search synonyms as drafting terms (they do not need `new_terms` entries unless you propose them for the specification). Under `### Search Concept Decomposition` give one sub-heading per search track (core architecture, individual components, functional equivalents, manufacturing method, use method, adjacent technologies) each with: concept statement, keyword families, CPC/IPC themes (mark any classification you are not certain of as an assumption), applicant/assignee categories, and ready-to-run search strings. The strings are for the human or a search tool; you do not run them. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
