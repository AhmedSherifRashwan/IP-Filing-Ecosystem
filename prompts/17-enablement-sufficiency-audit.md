---
id: "17"
title: "Enablement/sufficiency audit"
phase: P4
agent: ip-spec
kind: analysis
inputs: [confirmed_embodiments, out:P1/01, out:P1/03, terminology, out:P4/16]
optional_inputs: [out:P4/description, out:P2/09, intake:data/*]
deliverable_sections: ["Enablement Audit"]
timeout_s: 2400
include_rationale: false
---

*Check whether the skilled person can actually practise the scope.*

Audit the disclosure for enablement/sufficiency across the full intended claim breadth. Identify embodiments that would require undue experimentation, missing materials/process conditions, unsupported functional breadth, unpredictable technical areas, absent examples and parameters that need explanation. Recommend additional disclosure or experiments before filing.

**How to work.** Draft only from confirmed embodiments (`CONFIRMED.md`), the Phase 1 outputs and the differentiation matrix; unconfirmed Phase 3 candidates are not in front of you and must not reappear. Use the locked terminology verbatim. Never fabricate experimental data or results. This audit is a gate on your own breadth: scope you cannot enable does not go out described as enabled. In inner-loop round 1 audit the blueprint and the confirmed embodiments; when `description.md` is supplied (round 2 onwards) audit that text instead and cite its paragraph numbers. Under `### Enablement Audit` give a table: id (EN-1 ...) | scope element (locked term) | issue type (undue experimentation / missing conditions / functional breadth / unpredictable art / no example / parameter unexplained) | severity | what would cure it (additional disclosure the inventors can supply, an experiment, or narrowing the described scope). Recommendations that need inventor input go in `open_questions`; those you can cure from confirmed material go in `disclosure_requests` addressed to yourself for the compose or revise step. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
