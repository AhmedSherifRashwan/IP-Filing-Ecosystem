---
id: "05"
title: "Inventor interrogation"
phase: P1
agent: ip-disclosure
kind: analysis
inputs: [intake:disclosure, out:P1/01, out:P1/03, out:P1/04]
optional_inputs: [out:P1/02, intake:data/*]
deliverable_sections: ["Inventor Interview Questionnaire"]
timeout_s: 1200
include_rationale: false
---

*Expose missing disclosure before drafting.*

Generate a rigorous inventor interview questionnaire designed to uncover embodiments, alternatives, edge cases, parameter ranges, materials, geometries, sequences, failure modes, manufacturing options, control approaches and unexpected advantages not yet stated. Prioritise questions whose answers could materially broaden claim scope or provide fallback positions.

**How to work.** This questionnaire is what the human answers at Gate 1 (`00-intake/gate-1-answers.md`), so number every question (Q1, Q2 ...) and group them by theme. For each question state in one clause why the answer matters (broadens scope / provides fallback / closes enablement gap / resolves an `01` open question). Fold in every unresolved item from `01` (m) and every provisional classification from `03`, and use the preferred terms from the `04` table. Mark the questions whose answers block downstream phases with the phase in brackets, and mirror those in `open_questions`. Do not answer the questions yourself. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
