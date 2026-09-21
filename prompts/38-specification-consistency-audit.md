---
id: "38"
title: "Specification consistency audit"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:description, package:figures, package:registry, terminology, numeral_crosscheck]
optional_inputs: [package:claims]
deliverable_sections: ["Consistency Audit"]
timeout_s: 3600
include_rationale: false
---

*Eliminate internal contradictions.*

Search the final specification for inconsistent terminology, conflicting ranges, inconsistent reference numerals, mandatory language that conflicts with broader claims, statements that disparage or exclude viable embodiments, accidental admissions about prior art, and summary language narrower than the claims. List exact corrections. Cross-check against the locked terminology table (Prompt 4 / R2) and the reference-numeral registry (Prompt 20a / R4); report any drift from either as a corrected item, not a new finding.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. The locked terminology table and the machine-generated numeral cross-check are supplied as reference data: treat every avoid-term hit, undeclared synonym, or numeral mismatch they reveal as a `## Corrections` entry (`target: description`, exact location, `from`, `to`), not as a finding. Under `### Consistency Audit` give a table: location (paragraph / figure) | issue type | text (quoted) | correction. Substantive problems (mandatory language narrowing the claims, disparaging an embodiment, an admission about prior art, a summary narrower than the claims) are findings: `support` for narrowing or exclusion, `novelty` for an admission, with the paragraph cited. Do not rewrite the description. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
