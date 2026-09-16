---
id: "12"
title: "Equivalent architectures"
phase: P3
agent: ip-embodiment
kind: drafting
inputs: [out:P1/01, out:P1/03, out:P2/09, out:P2/10, terminology, intake:gate-1-answers]
optional_inputs: [out:P3/11, out:P1/02]
deliverable_sections: ["Equivalent Architecture Library"]
timeout_s: 1200
include_rationale: false
---

*Broaden the disclosure without relying on vague boilerplate.*

For every essential-looking component or step, identify functionally equivalent architectures that could achieve the same technical purpose. Describe concrete alternative structures, materials, mechanisms, ordering, coupling, actuation, sensing, processing and manufacturing approaches. Only include technically credible alternatives.

**How to work.** Everything you produce here is a candidate: it is not specification content until the human confirms at Gate 2 that it is technically real, so label the deliverable `Status: candidate` and never present an embodiment as something the inventors have already built or tested unless the dossier says so. Use the locked terminology verbatim; a new component name goes in `new_terms` with a reason. Only technically credible alternatives; anything that needs inventor confirmation is flagged in the deliverable and in `open_questions`. Take the features classified essential or advantageous in `03` as the list to cover. Under `### Equivalent Architecture Library` use one H4 per component or step (locked preferred term), then a numbered list of alternatives each with: concrete structure or mechanism, how it achieves the same technical purpose, what changes (interfaces, ordering, materials), the genus term that would embrace both, and a credibility note. Cross-reference the `11` design-around ids an alternative would close where `11` is supplied. Boilerplate lists ('any suitable material') are not alternatives. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
