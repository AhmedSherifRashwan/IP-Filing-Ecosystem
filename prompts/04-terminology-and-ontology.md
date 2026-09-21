---
id: "04"
title: "Terminology and ontology"
phase: P1
agent: ip-disclosure
kind: analysis
inputs: [intake:disclosure, out:P1/01, out:P1/03]
optional_inputs: [out:P1/02]
deliverable_sections: ["Terminology Table"]
timeout_s: 2400
include_rationale: false
---

*Prevent accidental narrowing and inconsistent language.*

Create a terminology table for the invention. For every important component or action provide: preferred neutral term, possible synonyms, broader genus terms, narrower species terms, functional descriptions and terms to avoid because they may unnecessarily narrow scope. Identify terms that require an express definition in the specification. This table becomes the locked terminology reference for every later drafting prompt (see R2).

**How to work.** This table is copied as-is into `terminology/LOCKED.md` at Gate 1 and parsed by machine, so the format is mandatory: under `### Terminology Table` emit exactly one markdown table with these seven columns in this order and these exact header spellings: `Preferred term | Synonyms | Broader (genus) | Narrower (species) | Functional description | Terms to avoid | Needs definition`. One row per component or action; separate multiple synonyms, genus, species or avoid-terms with semicolons; `Needs definition` is `yes` or `no`. Cover every component, step and parameter named in `01` and every feature in the `03` ladder, and use the same preferred term for each that `01` and `03` used unless you deliberately rename it, in which case record the rename in `new_terms`. Put any explanatory notes after the table, not inside it. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
