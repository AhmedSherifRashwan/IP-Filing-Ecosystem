---
id: "11"
title: "Competitor design-around attack"
phase: P3
agent: ip-embodiment
kind: drafting
inputs: [out:P1/01, out:P1/03, out:P2/09, out:P2/10, terminology, intake:gate-1-answers]
optional_inputs: [out:P1/02, out:P2/08]
deliverable_sections: ["Design-Around Risk Register"]
timeout_s: 2400
include_rationale: false
---

*Draft against foreseeable evasions.*

Assume you are a well-funded competitor who has read our proposed patent and wants the same commercial outcome without infringement. Generate at least 15 technically realistic design-arounds. For each, state which proposed claim limitation is avoided and how the specification could legitimately describe a broader genus or alternative embodiment to reduce that escape route.

**How to work.** Everything you produce here is a candidate: it is not specification content until the human confirms at Gate 2 that it is technically real, so label the deliverable `Status: candidate` and never present an embodiment as something the inventors have already built or tested unless the dossier says so. Use the locked terminology verbatim; a new component name goes in `new_terms` with a reason. Only technically credible alternatives; anything that needs inventor confirmation is flagged in the deliverable and in `open_questions`. The 'proposed claim limitations' are the nucleus and ladder features in `03` and the 'possible claim language' column of `09`. Under `### Design-Around Risk Register` give a table with at least 15 rows: id (DA-1 ...), the design-around, the limitation avoided, technical plausibility (high / medium / low, with reason), commercial attractiveness, the broader genus or alternative embodiment the specification could describe, and whether that description is already supported by the dossier or needs inventor confirmation. Rank rows by risk. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
