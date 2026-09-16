---
id: "26"
title: "Means/functional language audit"
phase: P5
agent: ip-claims
kind: drafting
inputs: [out:P4/description, registry, out:P4/19, out:P2/09, out:P1/02, terminology, out:P5/25]
optional_inputs: [out:P4/18]
deliverable_sections: ["Functional Language Audit"]
timeout_s: 1200
include_rationale: false
---

*Control interpretation risk.*

Identify every functional or result-oriented limitation. Assess whether it is adequately supported by disclosed structures/algorithms and whether jurisdiction-specific interpretation could narrow it. Where appropriate, propose structural alternatives and ensure the description discloses multiple ways of performing the claimed function.

**How to work.** The description in front of you is the only source of support. Any limitation that lacks an explicit passage in the description must NOT be drafted: leave it out, and record it in `disclosure_requests` (limitation, and the disclosure ip-spec would need to add) so the inner loop can supply the support. Use locked preferred terms verbatim and the registry's component names; where a claim term needs a reference sign, use the registry numeral. Cite the supporting paragraph for every limitation in `support_refs`. Audit the `### Final Claim Set` from `25`. Under `### Functional Language Audit` give a table: claim | quoted functional limitation | structures/algorithms disclosed for it (paragraphs) | count of disclosed ways | US means-plus-function / EPO clarity exposure | proposed structural alternative wording (using locked terms) | action (keep / reword / request disclosure). Where the description discloses only one way of performing a claimed function, that is a `disclosure_requests` entry for ip-spec, not a reason to narrow the claim. Do not rewrite the claim set here; proposed rewordings are picked up by the revise step. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
