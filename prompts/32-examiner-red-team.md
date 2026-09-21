---
id: "32"
title: "Examiner red-team (final)"
phase: REVIEW
agent: ip-reviewer
kind: review
inputs: [package:all]
optional_inputs: []
deliverable_sections: ["Ranked Objections"]
timeout_s: 3600
include_rationale: false
---

*Try to reject the application, against the exact filing text.*

Review the complete specification and claims as a hostile examiner. Identify the 20 strongest objections or weaknesses, ranked critical/high/medium/low, covering novelty, inventive step/non-obviousness, clarity, support, enablement, unity, eligibility, claim construction and formal inconsistencies. For each, propose the least narrowing corrective action. Run this prompt in the independent-reviewer context (Agent 7 / R8): supply only the finished specification and claims, not the drafting rationale.

**How to work.** You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale. The package in front of you (description, figures/figure instructions, claims, numeral registry, and the abstract once it exists) is exactly what an examiner, opponent or court would see; judge that text and nothing else. You have no prior-art references in front of you; novelty and inventive-step objections must therefore be built from what the description itself admits or describes as known, from common general knowledge you state explicitly as an assumption, and from the internal logic of the claims. Never cite a specific reference from memory. Under `### Ranked Objections` give exactly 20 objections, numbered and ordered by severity, each with: severity, ground (novelty / inventive step / clarity / support / enablement / unity / eligibility / claim construction / formal), the claim language quoted or paragraph cited, the attack, and the least-narrowing corrective action. Mirror every objection in `findings[]` with category `novelty`, `enablement`, `support`, `design-around` or `formal` and the matching `suggested_target`. Reply exactly in the review output contract: `## Findings` (opening with the H3 section(s) named above, then the ranked findings), `## Corrections` (may be empty), `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope whose `findings[]` and `corrections[]` mirror the prose. Every finding must quote the claim language or cite the paragraph/figure it attacks. You never edit the specification or claims and you never clear the application for filing.
