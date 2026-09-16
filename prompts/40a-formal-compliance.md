---
id: "40a"
title: "Formal / mechanical compliance pass"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: []
deliverable_sections: ["Formal Compliance Report"]
timeout_s: 1200
include_rationale: false
---

*Check filing mechanics separately from filing substance.*

Review the finished package against the target jurisdiction's formal requirements: paragraph numbering (e.g. USPTO [0001]-style), claim numbering and format, two-part claim form (preamble / characterising portion) where applicable, reference-sign conventions in claims, drawing sheet/margin/numbering rules, and any sequence-listing or similar formal annex requirements. List corrections as formal-only; do not re-raise substantive issues already captured in Prompt 30.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. The target jurisdictions are those named in the matter (default US, EPO, UK); check each. Under `### Formal Compliance Report` give a table: jurisdiction | requirement | status (pass / fail / not applicable) | location | correction. Every fail is both a `## Corrections` entry (`target: claims` or `description`, exact `from`/`to`) and a finding of category `formal`, severity `low` or `medium`. Do not raise anything substantive here. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
