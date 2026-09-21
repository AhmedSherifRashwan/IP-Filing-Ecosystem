---
id: "13"
title: "Parameter/range ladder"
phase: P3
agent: ip-embodiment
kind: drafting
inputs: [out:P1/01, out:P1/03, out:P2/09, out:P2/10, terminology, intake:gate-1-answers]
optional_inputs: [intake:data/*, intake:disclosure]
deliverable_sections: ["Parameter Ladders"]
timeout_s: 2400
include_rationale: false
---

*Create quantitative fallback positions.*

Identify every parameter that could matter: dimensions, concentration, temperature, pressure, time, force, frequency, dose, ratio, spacing, tolerances, thresholds and performance values. For each, propose a disclosure ladder containing broad range, intermediate ranges, narrower preferred ranges, endpoints and representative values, but only where scientifically supportable. Flag ranges requiring inventor confirmation or data.

**How to work.** Everything you produce here is a candidate: it is not specification content until the human confirms at Gate 2 that it is technically real, so label the deliverable `Status: candidate` and never present an embodiment as something the inventors have already built or tested unless the dossier says so. Use the locked terminology verbatim; a new component name goes in `new_terms` with a reason. Only technically credible alternatives; anything that needs inventor confirmation is flagged in the deliverable and in `open_questions`. Numbers are the highest-risk output of this phase. A value or range may be asserted only when it appears in the disclosure, the data files or a Gate-1 answer; every other rung is a proposal and must carry `needs confirmation` in the ladder. Under `### Parameter Ladders` give a table: parameter (locked term) | unit | broad range | intermediate range(s) | preferred range | endpoints | representative value(s) | source (passage / data file / proposed) | confirmation needed (yes / no). Do not invent measurement data and do not extrapolate beyond what the stated physics supports. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
