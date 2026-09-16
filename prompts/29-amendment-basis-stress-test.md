---
id: "29"
title: "Amendment basis stress test"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: []
deliverable_sections: ["Amendment Paths"]
timeout_s: 1200
include_rationale: false
---

*Prepare for prosecution before filing.*

Assume the broad independent claims are rejected. Generate 10 plausible amendment paths using only features expressly disclosed in the application as filed. For each path identify the exact combination support and explain whether intermediate generalisation or added-matter concerns could arise. Recommend specification additions before filing to strengthen amendment basis.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. Under `### Amendment Paths` give ten numbered paths, each with: the amended claim wording (quoted), the paragraphs/figures giving combination support, intermediate-generalisation or added-matter risk (none / possible / likely, with reason), and the specification addition that would remove that risk. Each recommended addition is a finding of category `support` targeting `P4`; you are recommending disclosure to add, not claim narrowing. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
