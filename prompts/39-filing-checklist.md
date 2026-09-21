---
id: "39"
title: "Filing checklist"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: [intake:priority/*]
deliverable_sections: ["Filing Checklist"]
timeout_s: 2400
include_rationale: false
---

*Create a final human sign-off.*

Generate a pre-filing checklist covering inventorship confirmation, applicant/ownership, priority details, drawings, sequence listings if applicable, examples/data labelling, claim count/dependencies, abstract, title, reference numerals, support matrix, prior-art disclosures/IDS considerations, assignment/confidentiality issues and jurisdiction-specific formalities for counsel to confirm.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. Under `### Filing Checklist` give a checkbox list grouped by the topics in the prompt; for each item state what the package shows (e.g. claim count, number of figures, whether examples are labelled actual/prophetic, whether an abstract is present and its word count) and what only a human can confirm (inventorship, ownership, assignment). Items the package already fails (e.g. abstract missing, a figure referenced but not instructed) are findings of category `formal`. The checklist is for counsel; it does not clear anything. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
