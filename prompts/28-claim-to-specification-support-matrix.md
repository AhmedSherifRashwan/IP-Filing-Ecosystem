---
id: "28"
title: "Claim-to-specification support matrix"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: []
deliverable_sections: ["Support Matrix"]
timeout_s: 3600
include_rationale: false
---

*Make every limitation traceable.*

For every limitation of every claim, create a support matrix identifying exact paragraph/section/figure support, whether support is explicit or implicit, and whether the limitation is disclosed in the claimed combination. Flag any claim language that lacks direct and unambiguous support or may create written-description/added-matter risk.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. Under `### Support Matrix` give one table covering every claim: claim | limitation (quoted) | support (paragraph numbers / figure) | explicit or implicit | disclosed in the claimed combination (yes / no) | risk note. Every row whose support is implicit only, absent, or not in the claimed combination becomes a finding of category `support` with the claim language quoted; recommend adding disclosure as the least-narrowing action and set `requires_new_disclosure` accordingly. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
