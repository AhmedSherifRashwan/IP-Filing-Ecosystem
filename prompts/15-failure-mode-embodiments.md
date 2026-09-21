---
id: "15"
title: "Failure-mode embodiments"
phase: P3
agent: ip-embodiment
kind: drafting
inputs: [out:P1/01, out:P1/03, out:P2/09, out:P2/10, terminology, intake:gate-1-answers]
optional_inputs: [out:P3/12, out:P3/13, intake:data/*]
deliverable_sections: ["Failure-Mode Embodiments"]
timeout_s: 2400
include_rationale: false
---

*Protect improvements that solve practical problems.*

List foreseeable failure modes, abuse cases, manufacturing variability, environmental conditions and user errors. For each, identify technical mitigations and whether those mitigations should be disclosed as alternative embodiments or dependent-claim fallbacks.

**How to work.** Everything you produce here is a candidate: it is not specification content until the human confirms at Gate 2 that it is technically real, so label the deliverable `Status: candidate` and never present an embodiment as something the inventors have already built or tested unless the dossier says so. Use the locked terminology verbatim; a new component name goes in `new_terms` with a reason. Only technically credible alternatives; anything that needs inventor confirmation is flagged in the deliverable and in `open_questions`. Under `### Failure-Mode Embodiments` give a table: id (FM-1 ...) | failure mode or abuse case | cause | technical mitigation | disclose as (alternative embodiment / dependent-claim fallback / both / not worth disclosing) | support (passage, or `candidate - needs confirmation`). Mitigations must be concrete structures, steps or control behaviours the inventors could plausibly have conceived; a mitigation that presumes test results the dossier does not contain is flagged, not asserted. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
