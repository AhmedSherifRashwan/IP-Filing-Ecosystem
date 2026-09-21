---
id: "23"
title: "Dependent-claim ladder"
phase: P5
agent: ip-claims
kind: drafting
inputs: [out:P4/description, registry, out:P4/19, out:P2/09, out:P1/02, terminology, out:P5/21, out:P5/22]
optional_inputs: [out:P2/08]
deliverable_sections: ["Dependent Claim Ladders"]
timeout_s: 3600
include_rationale: false
---

*Build fallback positions deliberately.*

For each independent claim create a dependent-claim ladder from broad to narrow. Cover structural relationships, materials, geometry, ranges, operating modes, control logic, manufacturing features and use conditions. Each dependent claim should add a meaningful fallback rather than cosmetic detail. Identify the technical effect associated with each added limitation.

**How to work.** The description in front of you is the only source of support. Any limitation that lacks an explicit passage in the description must NOT be drafted: leave it out, and record it in `disclosure_requests` (limitation, and the disclosure ip-spec would need to add) so the inner loop can supply the support. Use locked preferred terms verbatim and the registry's component names; where a claim term needs a reference sign, use the registry numeral. Cite the supporting paragraph for every limitation in `support_refs`. Under `### Dependent Claim Ladders` use one H4 per independent claim, then the dependent claims in full, numbered continuously after the independent claims of `22`, each followed by a one-line note: added limitation | technical effect | support paragraph | the prior-art or design-around risk it answers. Ranges come only from the description's stated ranges; do not create intermediate values the description does not recite. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
