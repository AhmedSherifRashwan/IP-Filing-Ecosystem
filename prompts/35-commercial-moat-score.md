---
id: "35"
title: "Commercial moat score"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: []
deliverable_sections: ["Moat Scores"]
timeout_s: 2400
include_rationale: false
---

*Prioritise claims that competitors cannot economically avoid.*

Score each independent claim concept from 1-10 for: commercial relevance, breadth, novelty resilience, inventive-step resilience, design-around resistance, infringement detectability, evidentiary ease and support strength. Identify which claims actually protect the commercially successful architecture and which are merely technically interesting.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. Under `### Moat Scores` give one table: independent claim | the eight scores | total | verdict (protects the commercial architecture / technically interesting only), followed by a short justification per claim that quotes the claim language that drives the score. Only a low design-around-resistance or detectability score becomes a finding (category `design-around`); the other scores are information for the human. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
