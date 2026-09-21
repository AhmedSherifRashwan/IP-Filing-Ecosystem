---
id: "20a"
title: "Reference-numeral registry & drawing generation"
phase: P4
agent: ip-spec
kind: drafting
inputs: [confirmed_embodiments, terminology, out:P4/16, out:P4/18, out:P4/20]
optional_inputs: [intake:drawings, out:P4/description, out:P5/claims, registry]
deliverable_sections: ["Figure Instructions", "Reference-Numeral Registry"]
timeout_s: 3600
include_rationale: false
---

*Turn the figure plan into consistent, generated figures - not just a plan.*

Using the figure plan from Prompt 20, generate the actual figures (or generation-ready drawing instructions for a draftsperson/CAD tool) for each planned view. Maintain a single reference-numeral registry mapping every numeral to a component name, the figure in which it is first introduced, and every figure in which it subsequently appears. Flag any numeral used inconsistently, any component named in the description or claims that lacks a numeral, and any figure that omits a numeral its description text relies on. This registry is cross-checked again in Prompt 38.

**How to work.** Draft only from confirmed embodiments (`CONFIRMED.md`), the Phase 1 outputs and the differentiation matrix; unconfirmed Phase 3 candidates are not in front of you and must not reappear. Use the locked terminology verbatim. Never fabricate experimental data or results. You cannot emit image files, so 'generate the actual figures' means generation-ready instructions: under `### Figure Instructions` give one H4 per figure (`FIG. 1` ...) with view, projection, every element drawn with its numeral, lead-line and callout notes, and anything a draftsperson or CAD tool needs to produce the sheet without asking. The registry is parsed by machine into `numerals/registry.json`, so under `### Reference-Numeral Registry` emit exactly one markdown table with these four columns in this order and these exact headers: `Numeral | Component | First figure | Also appears in` (component = locked preferred term; figures as `FIG. n`; `Also appears in` as a semicolon-separated list or `-`). Use a consistent scheme (e.g. 100-series for FIG. 1) and the same numeral for the same component in every figure. If an earlier registry is supplied, keep every existing numeral unchanged and only add. Put inconsistency flags (numeral reused, component in description/claims without a numeral, figure missing a numeral its text relies on) in a list after the table and in `open_questions`; when `description.md` or `claims.md` is not supplied, say the cross-check against them is deferred. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
