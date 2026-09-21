---
id: "02"
title: "Commercial value map"
phase: P1
agent: ip-disclosure
kind: analysis
inputs: [intake:disclosure, out:P1/01]
optional_inputs: [intake:data/*]
deliverable_sections: ["Commercial Value Map"]
timeout_s: 2400
include_rationale: false
---

*Make claim strategy follow the product and licensing opportunity.*

For this invention, identify the commercially valuable architectures, components, consumables, methods of manufacture, methods of use, software/control functions, systems and interfaces that competitors would need to practise. Rank them by commercial importance and explain which should be protected by independent claims versus dependent claims or separate claim families.

**How to work.** Work from the structured disclosure in `01` and the raw disclosure; do not assume a business model the inventor has not described, and say so in `assumptions` where you had to. Present the map as a ranked table (rank, subject matter, who would practise it, why it matters commercially, recommended protection: independent / dependent / separate family) followed by a short justification per row. Anything ranked on a guess about the market rather than the disclosure is an assumption with `blocking: false`. This map feeds Prompts 16, 21 and 22, so name each item with the component names used in `01`. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
