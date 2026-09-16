---
id: "39a"
title: "Abstract drafting"
phase: PACKAGE
agent: ip-spec
kind: compose
inputs: [package:description, package:claims]
optional_inputs: []
deliverable_sections: ["Abstract"]
timeout_s: 900
include_rationale: false
---

*Draft the required, format-constrained summary - last, and only from finished text.*

Using only the finalized detailed description and finalized claims (not the drafting history or earlier phase outputs), draft the Abstract: a single paragraph, approximately 50-150 words depending on jurisdiction, stating the technical field and the core technical solution in plain, non-legal language. Do not introduce new technical matter, do not use "means for" or other claim-style language, and do not include marketing or legal-conclusion language. Flag if the finalized claims changed after this draft was produced, since the abstract would then need to be re-checked.

**How to work.** You have been given only the finished description and the finished claims, on purpose; do not ask for or rely on anything else. Under `### Abstract` write exactly one paragraph of 50 to 150 words, no heading inside it, no reference numerals in parentheses unless the target jurisdiction convention requires them, no 'means for', no 'the invention', no 'novel' or 'improved', and no claim-style enumeration. Every technical statement in the abstract must be traceable to a claim or description paragraph; list those references in `support_refs`. State the word count in `## Rationale`. In `## Assumptions` record the claims file identity you drafted against so the runner can detect a later change. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
