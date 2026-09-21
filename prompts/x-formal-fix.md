---
id: "x-formal-fix"
title: "Apply formal corrections"
phase: REVIEW
agent: ip-spec
kind: revise
inputs: [corrections, package:claims, package:description]
optional_inputs: [registry, terminology]
deliverable_sections: ["Corrected Text", "Change Log"]
timeout_s: 2400
include_rationale: false
---

*Runner template: applies the reviewer's formal corrections (Prompts 37, 38, 40a) in place. The runner addresses this to ip-claims when the corrections target the claims and to ip-spec when they target the description.*

Apply only the listed formal corrections; change nothing else. The corrections embedded below each name a target (claims or description), an exact location, the exact text to replace and the exact replacement. Apply each one to the current package text embedded below, in order. Where a correction cannot be applied exactly as written (the `from` text is not found, or applying it would break antecedent basis, numbering or a reference numeral), do not improvise: leave the text unchanged for that item and report it.

**How to work.** Under `### Corrected Text` return the complete corrected text of the target document only (the whole claim set as numbered plain-text claims if the corrections target the claims; the whole numbered Description if they target the description; if the corrections list mixes both, return the document your agent owns and report the others as not applied). The runner replaces the package file with this section verbatim, so it must be whole and free of notes. Under `### Change Log` give a table: correction (in the order supplied) | location | applied (yes / no) | from (quoted) | to (quoted) | note. No substantive edits, no rewording beyond the listed corrections, no new matter, no renumbering unless a correction requires it. Use locked terms verbatim. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
