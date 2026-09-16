---
id: "22"
title: "Broad independent claims"
phase: P5
agent: ip-claims
kind: drafting
inputs: [out:P4/description, registry, out:P4/19, out:P2/09, out:P1/02, terminology, out:P5/21]
optional_inputs: [out:P2/07, out:P2/08]
deliverable_sections: ["Independent Claims", "Limitation Table"]
timeout_s: 1800
include_rationale: false
---

*Draft the commercial perimeter.*

Draft alternative independent claims directed to the core commercial architectures. Use the fewest limitations reasonably necessary to distinguish the known art while maintaining clarity, support and enablement. For each limitation provide: reason included, specification support, likely prior-art vulnerability and likely design-around route.

**How to work.** The description in front of you is the only source of support. Any limitation that lacks an explicit passage in the description must NOT be drafted: leave it out, and record it in `disclosure_requests` (limitation, and the disclosure ip-spec would need to add) so the inner loop can supply the support. Use locked preferred terms verbatim and the registry's component names; where a claim term needs a reference sign, use the registry numeral. Cite the supporting paragraph for every limitation in `support_refs`. Under `### Independent Claims` write each claim in full, numbered, in plain text (no markdown inside the claim), one claim per justified family from `21`. Under `### Limitation Table` give, for every limitation of every independent claim: claim | limitation (quoted) | reason included | specification support (paragraph numbers) | prior-art vulnerability (cite the `09` row or `07` chart) | design-around route. Test each limitation against 'is this needed to distinguish the charted art?'; if not, remove it and note the removal in `## Rationale`. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
