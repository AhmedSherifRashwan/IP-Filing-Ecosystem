---
id: "09"
title: "Differentiation matrix"
phase: P2
agent: ip-prior-art
kind: analysis
inputs: [out:P1/01, out:P1/03, out:P2/07, out:P2/08, terminology, intake:gate-1-answers]
optional_inputs: [intake:prior-art/*, out:P1/02]
deliverable_sections: ["Differentiation Matrix"]
timeout_s: 2400
include_rationale: false
---

*Find defensible technical distinctions.*

Create a matrix: prior-art feature -> our differentiating feature -> technical effect -> evidence/support -> possible claim language -> design-around risk. Prefer structural or process relationships that produce a technical effect over result-only language.

**How to work.** Analyse only the references supplied under `00-intake/prior-art/`; never cite or rely on a reference from memory, and if coverage looks thin say so in `open_questions` so the human can commission a professional search. Use the locked terminology verbatim. Read `gate-1-answers.md` as the inventor's authoritative resolution of the Phase 1 open questions. Emit the matrix as a single table under `### Differentiation Matrix` with exactly those six columns; one row per distinction, ordered by how much of the `08` attack it answers. 'Evidence/support' must cite the disclosure passage or `01`/`03` item (and the data file, if any) that establishes the feature and its effect; a distinction with no support in the dossier is not a distinction and belongs in `open_questions` instead. 'Possible claim language' is a phrase using locked terms, not a full claim. This matrix is a primary input to Phases 3, 4 and 5, so be concrete about the technical effect. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
