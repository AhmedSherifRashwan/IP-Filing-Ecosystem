---
id: "20"
title: "Drawings plan"
phase: P4
agent: ip-spec
kind: drafting
inputs: [confirmed_embodiments, out:P1/01, terminology, out:P4/16, out:P4/18]
optional_inputs: [intake:drawings, out:P4/19, out:P2/09]
deliverable_sections: ["Figure Plan"]
timeout_s: 1200
include_rationale: false
---

*Make figures carry disclosure value.*

Generate a figure plan that maximises technical disclosure: system overview, exploded views, cross-sections, process flow, alternative architectures, control logic, manufacturing sequence and use sequence. For each figure list reference numerals and the specific claim concepts/fallbacks it supports.

**How to work.** Draft only from confirmed embodiments (`CONFIRMED.md`), the Phase 1 outputs and the differentiation matrix; unconfirmed Phase 3 candidates are not in front of you and must not reappear. Use the locked terminology verbatim. Never fabricate experimental data or results. If the intake lists inventor drawings by file name, plan around them (say which planned figure each existing drawing becomes) but do not describe their content beyond what the disclosure text says. Under `### Figure Plan` give one H4 per figure (`FIG. 1`, `FIG. 2` ...) with: view type, what it shows, the confirmed embodiment(s) depicted, the components to be numbered (locked terms, no numerals yet - Prompt 20a assigns them), and the claim concepts or fallbacks it supports (cite blueprint section ids and `03` ladder rungs). Cover alternatives and combinations, not just the preferred embodiment. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
