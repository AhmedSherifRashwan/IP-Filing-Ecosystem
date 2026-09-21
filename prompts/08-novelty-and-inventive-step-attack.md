---
id: "08"
title: "Novelty and inventive-step attack (preliminary)"
phase: P2
agent: ip-prior-art
kind: analysis
inputs: [out:P1/01, out:P1/03, out:P2/07, terminology, intake:gate-1-answers]
optional_inputs: [out:P1/02, intake:prior-art/*]
deliverable_sections: ["Novelty and Inventive-Step Objections"]
timeout_s: 2400
include_rationale: false
---

*Think like a hostile examiner - early, to shape strategy, not as the final gate.*

Act as a highly sceptical patent examiner. Construct the strongest novelty and inventive-step/non-obviousness objections against each proposed independent claim concept using the identified prior art. Explain likely motivation-to-combine arguments, routine optimisation arguments, obvious alternatives and common-general-knowledge attacks. This is a diagnostic pass to inform differentiation and claim strategy; the comprehensive, final-text version of this exercise runs independently in Prompt 32.

**How to work.** Analyse only the references supplied under `00-intake/prior-art/`; never cite or rely on a reference from memory, and if coverage looks thin say so in `open_questions` so the human can commission a professional search. Use the locked terminology verbatim. Read `gate-1-answers.md` as the inventor's authoritative resolution of the Phase 1 open questions. There are no drafted claims yet: treat the nucleus (N0) and each ladder rung in `03`, and the top-ranked items in the value map if supplied, as the 'proposed independent claim concepts'. For each concept give the strongest novelty objection (single reference, citing the `07` chart row) and the strongest inventive-step objection (named combination, motivation, or common general knowledge), then rate each objection strong / moderate / weak with the reason. Common-general-knowledge assertions you cannot tie to a supplied reference must be labelled as assumptions. Do not propose claim wording here. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.

**If no prior-art references are embedded below**, the person supplied none. Say so explicitly, do not cite any reference from memory, build what can be built from the disclosure and the search strategy alone (mark it as untested against the art), set `status` to `partial`, and add an open question that a professional search is needed before filing.
