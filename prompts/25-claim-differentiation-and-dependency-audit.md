---
id: "25"
title: "Claim differentiation and dependency audit"
phase: P5
agent: ip-claims
kind: drafting
inputs: [out:P4/description, registry, out:P4/19, out:P2/09, out:P1/02, terminology, out:P5/22, out:P5/23, out:P5/24]
optional_inputs: [out:P5/21]
deliverable_sections: ["Audit", "Final Claim Set"]
timeout_s: 3600
include_rationale: false
---

*Remove redundancy and dependency errors.*

Audit the full claim set for antecedent basis, dependency, internal consistency, duplicate scope, accidental exclusion, unnecessary limitations, unclear relative terms, unsupported terminology, mixed statutory categories and contradictions. Recommend a clean dependency tree.

**How to work.** The description in front of you is the only source of support. Any limitation that lacks an explicit passage in the description must NOT be drafted: leave it out, and record it in `disclosure_requests` (limitation, and the disclosure ip-spec would need to add) so the inner loop can supply the support. Use locked preferred terms verbatim and the registry's component names; where a claim term needs a reference sign, use the registry numeral. Cite the supporting paragraph for every limitation in `support_refs`. The runner extracts the `### Final Claim Set` section of this reply verbatim into `claims.md`, which is what the independent reviewer and, eventually, counsel will read. So: under `### Audit` give the findings as a table (claim | issue type | problem | fix applied) and the clean dependency tree; under `### Final Claim Set` write the complete, corrected claim set as numbered claims in plain text - `1. A ...`, `2. The ... of claim 1, wherein ...` - one claim per paragraph, no tables, no commentary, no bold, no notes between claims, nothing in that section that is not claim text. Claims from `22`, `23` and the formulations selected in `24` are merged here; a limitation that the audit finds unsupported is removed and recorded in `disclosure_requests`, never left in. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
