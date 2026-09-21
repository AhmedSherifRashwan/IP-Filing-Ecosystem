---
id: "16"
title: "Specification blueprint"
phase: P4
agent: ip-spec
kind: drafting
inputs: [confirmed_embodiments, out:P1/01, out:P1/02, out:P1/03, terminology, out:P2/09]
optional_inputs: [out:P2/10, intake:data/*, intake:drawings]
deliverable_sections: ["Specification Blueprint", "Claim-Family Support Map"]
timeout_s: 3600
include_rationale: false
---

*Build the disclosure before polishing prose.*

Create a complete patent specification outline comprising title, technical field, background, problem, summary, brief description of drawings, detailed description, definitions, embodiments, examples, manufacturing/assembly, operation/use, alternatives, ranges, data/results and claim-support sections. Map every proposed claim family to the sections needed to support it.

**How to work.** Draft only from confirmed embodiments (`CONFIRMED.md`), the Phase 1 outputs and the differentiation matrix; unconfirmed Phase 3 candidates are not in front of you and must not reappear. Use the locked terminology verbatim. Never fabricate experimental data or results. Under `### Specification Blueprint` give the outline as a nested list with a stable section id for each node (S1, S1.1 ...), a one-line statement of what the section will contain and which dossier material feeds it. Under `### Claim-Family Support Map` give a table: claim family (from the value map: apparatus, system, method of use, method of manufacture, composition, kit, consumable, controller/software, product-by-process as justified) | sections that must support it | confirmed embodiments relied on | gaps. This blueprint drives Prompts 17-20a and the description compose step, so the section ids must be reused there. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
