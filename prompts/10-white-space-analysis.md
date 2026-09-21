---
id: "10"
title: "White-space analysis"
phase: P2
agent: ip-prior-art
kind: analysis
inputs: [intake:disclosure, out:P1/01, out:P1/03, out:P2/09, terminology, intake:gate-1-answers]
optional_inputs: [intake:prior-art/*, out:P2/08, out:P1/02]
deliverable_sections: ["White-Space Opportunities"]
timeout_s: 2400
include_rationale: false
---

*Find protectable territory beyond the first concept.*

Based on the invention and prior art, identify technical white space that is genuinely supported by the inventor's disclosure. Look for novel sub-combinations, interfaces, sequencing, geometries, control schemes, manufacturing steps, calibration methods, disposable/reusable divisions and use protocols. Do not invent unsupported subject matter.

**How to work.** Analyse only the references supplied under `00-intake/prior-art/`; never cite or rely on a reference from memory, and if coverage looks thin say so in `open_questions` so the human can commission a professional search. Use the locked terminology verbatim. Read `gate-1-answers.md` as the inventor's authoritative resolution of the Phase 1 open questions. Every opportunity listed must point to the disclosure passage (or Gate-1 answer) that already supports it; an idea that would need new inventor input is not white space, it is a question, and goes in `open_questions` marked as blocking `P3`. Under `### White-Space Opportunities` give a numbered list, each with: the sub-combination or aspect, why it is clear of the charted references (cite `07`/`09`), the supporting passage, and a commercial note. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
