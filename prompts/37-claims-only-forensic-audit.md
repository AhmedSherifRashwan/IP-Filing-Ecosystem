---
id: "37"
title: "Claims-only forensic audit"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:claims]
optional_inputs: [terminology]
deliverable_sections: ["Forensic Audit"]
timeout_s: 1200
include_rationale: false
---

*Check the exact final claim text.*

For the final claims below the material embedded under 'Dossier files' below, perform a line-by-line forensic audit for antecedent basis, grammar, numbering, dependency, multiple dependency, claim category, consistency of terminology, optional language, relative terms, functional language, unnecessary limitations, unsupported terms and possible interpretation traps. Return a corrected claim set plus a change log.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. You are given the claims only. Under `### Forensic Audit` give a table: claim | line or phrase (quoted) | defect type | problem. You do not edit the claims: the 'corrected claim set plus a change log' is delivered as `## Corrections` entries, one per defect, each with `target: claims`, the exact location, the exact `from` text and the exact `to` text, so the claims drafter can apply them mechanically; the change log is that list. Findings from this prompt are category `formal` unless the defect is substantive (an unsupported term is `support`; an unnecessary limitation is `design-around`). Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
