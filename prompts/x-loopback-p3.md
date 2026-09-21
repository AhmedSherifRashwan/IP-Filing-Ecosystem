---
id: "x-loopback-p3"
title: "Loop-back into Phase 3"
phase: P3
agent: ip-embodiment
kind: drafting
inputs: [findings:novelty, findings:design-around, out:P3/11, out:P3/12, confirmed_embodiments, terminology, out:P4/description]
optional_inputs: [out:P3/13, out:P3/14, out:P3/15, out:P5/claims, out:P2/09]
deliverable_sections: ["Additional Embodiments", "Design-Around Responses"]
timeout_s: 3600
include_rationale: false
---

*Runner template: re-entry into Phase 3 after the independent reviewer's novelty or design-around findings. Respond to the findings; do not restart Phase 3.*

The independent reviewer has raised the novelty and design-around findings embedded below against the finished Description and claims. For each finding, and only for those findings, propose the additional embodiments, alternative architectures, genus descriptions or sub-combinations that would answer it: for a novelty finding, the technically credible distinguishing feature or relationship the inventors have conceived that the Description does not yet express; for a design-around finding, the broader genus or alternative embodiment that closes the escape route. Map every proposal to the finding id it answers, to the existing design-around register entry or equivalent-architecture entry it extends, and to the passage of the current Description where it would be inserted.

**How to work.** Everything here is a candidate again: the human will re-confirm at Gate 2 (delta only) before ip-spec writes any of it into the Description, so label each proposal `Status: candidate` and state whether it is already implied by a confirmed embodiment or is genuinely new matter needing inventor confirmation. Do not re-run Prompts 11-15, do not re-open confirmed embodiments, and do not propose anything unconnected to a supplied finding. Under `### Additional Embodiments` give a numbered list (finding id | embodiment | concrete structure or step | genus term | insertion point in the Description | confirmation needed). Under `### Design-Around Responses` give a table (finding id | design-around | limitation avoided | response embodiment or genus | closes the route fully / partly | confirmation needed). Where you believe a finding cannot be answered without narrowing, say so in `open_disagreements` rather than inventing an embodiment. Use locked terms verbatim. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
