---
id: "07"
title: "Prior-art claim chart"
phase: P2
agent: ip-prior-art
kind: analysis
inputs: [out:P1/01, out:P1/03, terminology, intake:gate-1-answers]
optional_inputs: [out:P1/04, out:P2/06, intake:prior-art/*]
deliverable_sections: ["Prior-Art Claim Charts"]
timeout_s: 2400
include_rationale: false
---

*Map what is actually disclosed rather than relying on abstracts.*

For each supplied prior-art reference the material embedded under 'Dossier files' below, create an element-by-element chart against the proposed inventive concepts. Distinguish explicit disclosure, implicit/inherent disclosure, absent features and ambiguous features. Quote or cite the precise passages/figures where available. Do not combine references when assessing strict novelty; separately analyse combinations for inventive step/non-obviousness.

**How to work.** Analyse only the references supplied under `00-intake/prior-art/`; never cite or rely on a reference from memory, and if coverage looks thin say so in `open_questions` so the human can commission a professional search. Use the locked terminology verbatim. Read `gate-1-answers.md` as the inventor's authoritative resolution of the Phase 1 open questions. Produce one chart per reference under `### Prior-Art Claim Charts` (H4 = reference file name): rows are the nucleus and ladder features from `03`, columns are disclosure status (explicit / implicit-inherent / absent / ambiguous), the quoted passage or figure with its location, and a note. Where a reference is an abstract or excerpt only, chart what is there and flag the gap; never guess what the full document says. Keep the strict-novelty charts single-reference; put any combination analysis in a clearly separate closing sub-section. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.

**If no prior-art references are embedded below**, the person supplied none. Say so explicitly, do not cite any reference from memory, build what can be built from the disclosure and the search strategy alone (mark it as untested against the art), set `status` to `partial`, and add an open question that a professional search is needed before filing.
