---
id: "30"
title: "US/EPO/UK resilience review"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: []
deliverable_sections: ["Jurisdictional Resilience Review"]
timeout_s: 1200
include_rationale: false
---

*Expose jurisdiction-specific drafting weaknesses.*

Review the draft for major US, EPO and UK prosecution risks, including written description/support, enablement/sufficiency, clarity/definiteness, added matter, unity, excluded subject matter/patent eligibility, functional claiming, multiple dependencies and claim fees/format considerations. Do not give a definitive legal conclusion; identify issues for counsel review.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. Under `### Jurisdictional Resilience Review` use three H4s (`US`, `EPO`, `UK`) and under each list the issues with the claim or paragraph cited. Categorise findings by their substance (support / enablement / design-around / novelty) rather than by jurisdiction; purely formal items (multiple-dependency format, claim fees) are category `formal`. Phrase everything as an issue for counsel, never as a legal conclusion. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
