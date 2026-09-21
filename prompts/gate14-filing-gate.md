---
id: "gate14"
title: "The 14-question filing gate"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: [intake:priority/*]
deliverable_sections: ["Fourteen-Question Gate"]
timeout_s: 2400
include_rationale: false
---

*Answer the playbook's fourteen filing-gate questions against the exact package, as a structured checklist for the human.*

Answer each of the following questions about the finished package with yes, no or unclear, and give the evidence from the package for each answer:

1. Do the independent claims cover the product or architecture that would actually make money?
2. Can a competitor obtain substantially the same technical/commercial result while omitting one claim element?
3. Are the likely design-arounds expressly disclosed as alternatives where genuinely conceived?
4. Does every claim limitation have clear support in the application as filed, including the claimed combination?
5. Are there enough narrower fallback positions to survive the most credible prior art?
6. Does the disclosure enable the intended breadth without undue experimentation?
7. Have quantitative ranges, endpoints, intermediate ranges and representative values been captured where relevant?
8. Are manufacturing, use, system, device, consumable and software/control aspects protected where commercially justified?
9. Will infringement of the key claims be detectable from the competitor's product, documentation or process?
10. Have priority support and potential later-added subject matter been separated?
11. Have potential continuation/divisional themes been disclosed sufficiently in the first filing?
12. Does the Abstract accurately summarise the finalized claims with no new technical matter, legal phrases or "means for" language?
13. Has the formal/mechanical compliance pass (paragraph numbering, claim format, reference signs, drawing rules) been completed for the target jurisdiction(s)?
14. Has qualified patent counsel reviewed jurisdiction-specific legal and formal requirements before filing?

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. Under `### Fourteen-Question Gate` give a table: q | question (short form) | answer (yes / no / unclear) | evidence (quoted claim language or paragraph/figure citations). The envelope's `gate14[]` must contain exactly fourteen entries with `q` 1 to 14 and the same answers. Questions 1 and 9 depend on commercial facts you do not have: answer from the package text and mark the dependency in the evidence. Question 14 is always `no` unless the package itself records counsel's review; you cannot answer it on counsel's behalf. A `no` on questions 2 to 8 or 12 to 13 should also appear as a finding of the matching category unless one already exists in this review. This gate informs Human Gate 3; it does not replace it. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
