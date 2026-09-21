---
id: "14"
title: "Combination/permutation map"
phase: P3
agent: ip-embodiment
kind: drafting
inputs: [out:P1/01, out:P1/03, out:P2/09, out:P2/10, terminology, intake:gate-1-answers, out:P3/12]
optional_inputs: [out:P3/13, out:P1/02]
deliverable_sections: ["Combination Matrix"]
timeout_s: 2400
include_rationale: false
---

*Support future claim combinations.*

Create a feature-combination matrix showing which optional features can be combined with each core embodiment. Identify combinations likely to be commercially important or patentably distinct. Draft specification language that expressly supports these combinations without asserting technically incompatible permutations.

**How to work.** Everything you produce here is a candidate: it is not specification content until the human confirms at Gate 2 that it is technically real, so label the deliverable `Status: candidate` and never present an embodiment as something the inventors have already built or tested unless the dossier says so. Use the locked terminology verbatim; a new component name goes in `new_terms` with a reason. Only technically credible alternatives; anything that needs inventor confirmation is flagged in the deliverable and in `open_questions`. Core embodiments are the nucleus ladder rungs in `03` plus the alternatives in `12`; optional features are those classified advantageous / exemplary in `03` and the white-space items in `10`. Under `### Combination Matrix` emit the matrix (rows = optional features, columns = core embodiments, cells = compatible / incompatible / conditional with a footnote for conditional and incompatible cells), then a list of the commercially important or patentably distinct combinations, then the draft specification sentences that expressly support them, written with locked terms. Never write a sentence that asserts an incompatible permutation. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
