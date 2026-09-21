---
id: "24"
title: "Multiple independent formulations"
phase: P5
agent: ip-claims
kind: drafting
inputs: [out:P4/description, registry, out:P4/19, out:P2/09, out:P1/02, terminology, out:P5/22]
optional_inputs: [out:P5/23, out:P2/08]
deliverable_sections: ["Three Formulations", "Comparison"]
timeout_s: 2400
include_rationale: false
---

*Avoid betting the patent on one wording.*

Draft 3 materially different independent-claim approaches to the same inventive concept: (1) structural architecture, (2) functional/relationship-based architecture, and (3) process or interaction architecture where support permits. Compare breadth, clarity, prior-art exposure, enablement risk and design-around resistance.

**How to work.** The description in front of you is the only source of support. Any limitation that lacks an explicit passage in the description must NOT be drafted: leave it out, and record it in `disclosure_requests` (limitation, and the disclosure ip-spec would need to add) so the inner loop can supply the support. Use locked preferred terms verbatim and the registry's component names; where a claim term needs a reference sign, use the registry numeral. Cite the supporting paragraph for every limitation in `support_refs`. Under `### Three Formulations` use H4s `Formulation 1 - structural`, `Formulation 2 - functional/relationship`, `Formulation 3 - process/interaction`, each with the full claim text in plain text and its support paragraphs; if support does not permit a formulation, say so under its heading rather than forcing one. Under `### Comparison` give a table with the five criteria as columns and a recommendation row stating which formulation(s) should go forward into the final set and why. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
