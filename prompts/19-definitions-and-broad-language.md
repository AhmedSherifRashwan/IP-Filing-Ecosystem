---
id: "19"
title: "Definitions and broad language"
phase: P4
agent: ip-spec
kind: drafting
inputs: [confirmed_embodiments, out:P1/01, out:P1/03, terminology, out:P4/16]
optional_inputs: [out:P4/18, out:P2/09]
deliverable_sections: ["Definitions"]
timeout_s: 1200
include_rationale: false
---

*Create deliberate breadth, not ambiguity.*

Draft definitions for key terms that clarify scope while remaining technically credible. Identify where "comprising", optionality, singular/plural usage, direct/indirect coupling, ranges, approximately/about, order of steps and functional terminology need explicit treatment. Avoid definitions that contradict ordinary technical meaning or the embodiments.

**How to work.** Draft only from confirmed embodiments (`CONFIRMED.md`), the Phase 1 outputs and the differentiation matrix; unconfirmed Phase 3 candidates are not in front of you and must not reappear. Use the locked terminology verbatim. Never fabricate experimental data or results. Start from the rows of the locked table marked `Needs definition: yes`; you may add definitions for other locked terms but not define a term that is not locked without a `new_terms` entry. Under `### Definitions` give each definition as a self-contained paragraph in the form the specification will use ('As used herein, "X" means ...'), followed by a sub-section of general-construction paragraphs covering comprising, optionality, singular/plural, direct/indirect coupling, ranges and endpoints, about/approximately, order of steps and functional terminology. Each definition must be consistent with every confirmed embodiment; where a broad definition would read on an embodiment the dossier excludes, narrow the definition, not the embodiment, and say why in `## Rationale`. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
