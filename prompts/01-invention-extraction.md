---
id: "01"
title: "Invention extraction"
phase: P1
agent: ip-disclosure
kind: analysis
inputs: [intake:disclosure]
optional_inputs: [intake:data/*, intake:drawings]
deliverable_sections: ["Structured Invention Disclosure"]
timeout_s: 2400
include_rationale: false
---

*Convert the inventor's raw concept into a structured invention disclosure.*

Review the following invention disclosure: the material embedded under 'Dossier files' below. Extract: (a) problem being solved; (b) core inventive concept; (c) technical effect; (d) essential components/steps; (e) optional components; (f) alternative implementations; (g) inputs/outputs; (h) materials, dimensions, ranges and operating conditions; (i) control logic/software; (j) manufacturing methods; (k) use cases; (l) experimental evidence; and (m) unresolved technical questions. Do not draft claims yet.

**How to work.** Rely on `00-intake/disclosure.md` as the sole source of technical fact; the data files and the drawing-name listing, if present, are corroboration only. Under `### Structured Invention Disclosure` use one H4 per item (a) to (m) so later prompts can cite them. Every item the disclosure does not answer goes under (m) and is repeated in `open_questions` with the phases it blocks (`P2`, `P3`, `P4`, `P5`); never fill a gap with a plausible technical fact. Mark anything you infer rather than read as an assumption. This runs before the terminology lock, so choose neutral component names and reuse them consistently within the reply. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
