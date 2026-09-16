---
id: "21"
title: "Claim-family strategy"
phase: P5
agent: ip-claims
kind: drafting
inputs: [out:P4/description, registry, out:P4/19, out:P2/09, out:P1/02, terminology]
optional_inputs: [out:P2/07, out:P1/03, out:P4/16]
deliverable_sections: ["Claim-Family Architecture"]
timeout_s: 1200
include_rationale: false
---

*Protect the invention from multiple infringement angles.*

Propose a claim-family architecture covering, where appropriate: apparatus/device, system, method of operation/use, method of manufacture, composition/material, kit, consumable, controller/software/computer-implemented method, data-processing method and product-by-process. State which families are justified by the disclosure and commercial model; do not add claim categories merely for volume.

**How to work.** The description in front of you is the only source of support. Any limitation that lacks an explicit passage in the description must NOT be drafted: leave it out, and record it in `disclosure_requests` (limitation, and the disclosure ip-spec would need to add) so the inner loop can supply the support. Use locked preferred terms verbatim and the registry's component names; where a claim term needs a reference sign, use the registry numeral. Cite the supporting paragraph for every limitation in `support_refs`. Under `### Claim-Family Architecture` give a table: family | justified (yes / no) | commercial basis (cite the value-map rank) | disclosure basis (description paragraphs) | intended independent claim concept | notes on jurisdiction (e.g. unity, category mixing). A family with a commercial case but no disclosure basis is not justified; put it in `disclosure_requests` instead. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
