---
id: "27"
title: "Omnibus fallback claim concepts"
phase: P5
agent: ip-claims
kind: drafting
inputs: [out:P4/description, registry, out:P4/19, out:P2/09, out:P1/02, terminology, out:P5/25]
optional_inputs: [out:P2/10, out:P5/21, out:P4/16]
deliverable_sections: ["Omnibus Fallback Concepts"]
timeout_s: 2400
include_rationale: false
---

*Capture sub-combinations worth preserving.*

Identify independently valuable sub-combinations disclosed in the specification that could support later amendment, continuation/divisional practice or separate inventions. Draft candidate claim concepts and identify exact supporting passages/figures needed in the initial filing.

**How to work.** The description in front of you is the only source of support. Any limitation that lacks an explicit passage in the description must NOT be drafted: leave it out, and record it in `disclosure_requests` (limitation, and the disclosure ip-spec would need to add) so the inner loop can supply the support. Use locked preferred terms verbatim and the registry's component names; where a claim term needs a reference sign, use the registry numeral. Cite the supporting paragraph for every limitation in `support_refs`. Under `### Omnibus Fallback Concepts` give a numbered list; each concept has: the sub-combination in one sentence, a candidate independent-claim concept (claim-like prose, not necessarily a finished claim), the exact description paragraphs and figures that support it today, whether it is fully supported for a future continuation/divisional or needs a passage added before filing (then also a `disclosure_requests` entry), and the white-space item from `10` it corresponds to, if any. These do not enter `claims.md`. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
