---
id: "34"
title: "Freedom-to-operate separation"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: []
deliverable_sections: ["FTO Issue List"]
timeout_s: 1200
include_rationale: false
---

*Avoid confusing patentability with freedom to operate.*

Separately identify third-party patents that could potentially be practised by our contemplated product even if our invention is patentable. Explain that patentability does not establish freedom to operate. Produce an FTO issue list for professional claim-chart review, focusing on live claims and relevant jurisdictions.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. You have no third-party patents in front of you and must not name any from memory. Under `### FTO Issue List` therefore identify, from the description and claims, the product features, components, methods and materials that a professional FTO search should target, with the jurisdictions of interest and why each item is likely to sit inside someone else's live claims. Open with the sentence that patentability does not establish freedom to operate. This prompt normally produces open questions for counsel rather than routable findings; if you do raise a finding, category `design-around`, severity at most `medium`. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
