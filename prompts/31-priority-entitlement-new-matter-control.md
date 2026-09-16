---
id: "31"
title: "Priority entitlement/new matter control"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: [intake:priority/*]
deliverable_sections: ["Priority-Support Matrix"]
timeout_s: 1200
include_rationale: false
---

*Protect the priority chain.*

Compare the intended claims against the earliest priority disclosure. Identify every limitation or combination that may not be supported in the priority document and therefore may lose priority. Separate genuinely new matter that should only receive the later filing date. Produce a priority-support matrix.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. If no priority document is supplied under `00-intake/priority/`, say so in the first line, return `status: partial` with one open question, and do not invent a priority disclosure. Otherwise, under `### Priority-Support Matrix` give a table: claim | limitation (quoted) | supported in priority document (yes / partial / no) | passage in priority document | consequence (keeps priority / at risk / new matter - later date only). Each 'partial' or 'no' row is a finding of category `support`. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
