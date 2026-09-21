---
id: "33"
title: "Litigation/enforcement red-team"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: []
deliverable_sections: ["Enforcement Analysis"]
timeout_s: 2400
include_rationale: false
---

*Ask whether the claims will matter after grant.*

Assume the patent is granted and a major competitor launches a close product. Analyse which claim elements will be easiest to avoid, hardest to prove, hidden inside a process, dependent on intent, or difficult to detect from a commercial product. Recommend claim formulations that improve detectability and enforceability while remaining supported.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. Under `### Enforcement Analysis` give a table over every independent-claim element: claim | element (quoted) | avoidability | provability | hidden-in-process | intent-dependent | detectability from product/documentation | recommended reformulation (only if supported - cite the paragraph). Each weakness worth acting on is a finding of category `design-around`; if the fix needs disclosure the package lacks, set `requires_new_disclosure: true` and target `P3`, otherwise target `P5`. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
