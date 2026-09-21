---
id: "03"
title: "Inventive nucleus"
phase: P1
agent: ip-disclosure
kind: analysis
inputs: [intake:disclosure, out:P1/01, out:P1/02]
optional_inputs: [intake:data/*]
deliverable_sections: ["Inventive Nucleus", "Feature Classification"]
timeout_s: 2400
include_rationale: false
---

*Separate the true inventive concept from implementation detail.*

Identify the minimum technical combination that appears to create the new technical effect. Then produce progressively narrower versions adding implementation features. For each feature state whether it is potentially essential, advantageous, conventional, or merely exemplary. Flag any feature whose omission may undermine enablement or technical effect.

**How to work.** State the nucleus under `### Inventive Nucleus` as one minimum combination in plain technical language, then the progressively narrower versions as a numbered ladder (N0 = nucleus, N1, N2 ...). Under `### Feature Classification` give a table: feature | classification (essential / advantageous / conventional / exemplary) | why | omission risk (enablement / technical effect / none). Every classification must point to the passage in `01` or the disclosure that supports it; where the disclosure is silent on whether a feature is essential, classify it provisionally, mark it as an assumption and add an open question for Gate 1. Do not draft claims. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
