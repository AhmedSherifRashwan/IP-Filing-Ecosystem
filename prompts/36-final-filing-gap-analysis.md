---
id: "36"
title: "Final filing gap analysis"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: []
deliverable_sections: ["Gap Analysis"]
timeout_s: 2400
include_rationale: false
---

*Last pre-filing completeness check.*

Perform a final gap analysis of the exact filing package. Ask: What would we regret not disclosing if a competitor exploited it 18 months from now? Identify missing embodiments, definitions, combinations, ranges, drawings, examples, manufacturing alternatives, control logic, use cases, claim families and fallback positions that are already within the inventors' conception and should be added before filing.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. Under `### Gap Analysis` list each gap with: type (embodiment / definition / combination / range / drawing / example / manufacturing / control logic / use case / claim family / fallback), where in the package the gap shows (paragraph or claim), why it matters in 18 months, and the evidence in the package itself that the inventors have conceived the missing matter (you cannot know their conception beyond what the text implies, so say 'appears within conception because ...'). Gaps become findings of category `enablement` (missing conditions/examples) or `support` (missing embodiment/combination/range), always with `requires_new_disclosure: true`. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
