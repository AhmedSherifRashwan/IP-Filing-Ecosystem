---
id: "18"
title: "Best implementation/examples"
phase: P4
agent: ip-spec
kind: drafting
inputs: [confirmed_embodiments, out:P1/01, out:P1/03, terminology, out:P4/16, out:P4/17]
optional_inputs: [intake:data/*, intake:drawings, out:P2/09]
deliverable_sections: ["Worked Examples"]
timeout_s: 1800
include_rationale: false
---

*Capture concrete working embodiments.*

For each principal embodiment, draft a technically detailed worked implementation including components, materials, dimensions/ranges, fabrication/assembly, operating sequence, inputs/outputs and expected technical effect. Separate actual experimental examples from prophetic examples and do not fabricate results.

**How to work.** Draft only from confirmed embodiments (`CONFIRMED.md`), the Phase 1 outputs and the differentiation matrix; unconfirmed Phase 3 candidates are not in front of you and must not reappear. Use the locked terminology verbatim. Never fabricate experimental data or results. Under `### Worked Examples` use exactly two H4 headings, `#### Actual examples` and `#### Prophetic examples`, and put every example under one of them. An example is 'actual' only if the disclosure or the data files record that it was built or run and give the result; quote or cite that source next to every reported value. A prophetic example is written in the present or conditional tense, never reports a measured result, and states which confirmed embodiment it instantiates. Number examples (Example 1 ...) and give each the components, materials, dimensions/ranges, fabrication/assembly, operating sequence, inputs/outputs and expected technical effect in that order. If a principal embodiment has neither kind of example, say so and add a `disclosure_requests` entry rather than inventing one. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
