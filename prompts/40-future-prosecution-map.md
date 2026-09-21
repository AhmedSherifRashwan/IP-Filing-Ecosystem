---
id: "40"
title: "Future prosecution map"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: []
deliverable_sections: ["Prosecution Roadmap"]
timeout_s: 2400
include_rationale: false
---

*Preserve strategic optionality.*

Based solely on subject matter disclosed in this application, create a prosecution roadmap identifying the primary claim set, likely fallback amendments, possible continuation/divisional claim themes, inventions that may trigger lack-of-unity/restriction, and commercially important subject matter that should not be inadvertently abandoned. Do not assume later-added subject matter can obtain the original filing date.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. Under `### Prosecution Roadmap` use H4s `Primary claim set`, `Fallback amendments`, `Continuation/divisional themes`, `Unity/restriction risks`, `Do not abandon`; cite the supporting paragraphs and figures for every theme so a later filing can rely on them. Themes without adequate support in the package are findings of category `support` with `requires_new_disclosure: true`; everything else is information for counsel, not a finding. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
