---
id: "x-p4-compose-description"
title: "Compose the Description"
phase: P4
agent: ip-spec
kind: compose
inputs: [out:P4/16, out:P4/17, out:P4/18, out:P4/19, out:P4/20, out:P4/20a, confirmed_embodiments, terminology, registry]
optional_inputs: [out:P1/01, intake:drawings]
deliverable_sections: ["Description"]
timeout_s: 5400
include_rationale: false
---

*Runner template: the playbook has no prompt that emits the finished Description prose. This step assembles it from the Phase 4 outputs.*

Assemble the finished patent Description from the Phase 4 outputs embedded below: follow the section order and section ids of the specification blueprint (Prompt 16); honour the enablement audit (Prompt 17) by describing only what the audit found enabled and by including the additional disclosure it recommended where that disclosure is present in the confirmed embodiments; incorporate the worked examples (Prompt 18) with their actual / prophetic labelling intact; include the definitions (Prompt 19) as written; write the brief description of the drawings from the figure plan (Prompt 20) and the figure instructions (Prompt 20a); and refer to every component by its locked preferred term followed by its reference numeral from the registry on first mention in each paragraph. Produce, in order: title; technical field; background; summary; brief description of the drawings; detailed description (embodiments, definitions, examples, manufacturing/assembly, operation/use, alternatives and combinations, ranges, data/results where actual data exists); and a closing paragraph on the scope of alternatives. Number every paragraph of the body consecutively in the form [0001], [0002] ... starting at the technical field.

**How to work.** This is composition, not invention: every technical statement in the Description must already appear in one of the embedded Phase 4 outputs or in `CONFIRMED.md`; add no new technical matter, no new ranges, no new examples and no results. Where the inputs disagree, the enablement audit governs breadth, the definitions govern meaning, the registry governs numerals, and the locked terminology governs names - record each such resolution in `## Rationale`. The runner writes the `### Description` section verbatim to `description.md`, which the claims drafter and later the blind reviewer will read, so that section must contain only the Description text itself: no editorial notes, no bracketed placeholders, no headings other than the specification's own section headings, and paragraph numbers on every body paragraph. Anything the blueprint calls for that the inputs do not supply is omitted from the text and listed in `disclosure_requests` (for the human or the next round), never filled in. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
