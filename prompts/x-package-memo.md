---
id: "x-package-memo"
title: "Cover memo for counsel"
phase: PACKAGE
agent: ip-orchestrator
kind: prose
inputs: [package:all, findings:all, out:REVIEW/gate14, out:REVIEW/39]
optional_inputs: [inner_loop_flags, open_disagreements, out:REVIEW/40]
deliverable_sections: []
timeout_s: 1200
include_rationale: false
---

*Runner template: the cover memo that accompanies the filing-ready package to qualified patent counsel at Gate 3.*

Write the cover memo that accompanies this package to qualified patent counsel. Cover, under these headings: `## What is in the package` (the Abstract, Description, Figures, Claims, numeral registry, support matrix, priority matrix, filing checklist and the fourteen-question gate, with file names, claim count, figure count and abstract word count); `## Residual findings` (every finding still open after review and loop-back, with id, severity, category, the claim or paragraph it concerns and the least-narrowing action the reviewer proposed - medium and low findings included); `## What was never confirmed` (candidate embodiments, ranges, or disclosure requests that reached the package without human confirmation, inner-loop disagreements still open, and any loop-back cap decision the human took, quoting the decision); `## Fourteen-question gate` (the fourteen answers with their evidence, verbatim from the reviewer); and `## Counsel review required`.

**How to work.** State the facts of the package as they are; do not soften a residual finding, do not summarise away a `no` on the gate, and do not add any technical statement that is not in the package or the review. The final section must contain, verbatim, the sentence: 'No agent in this system may authorise filing; this package is a drafting aid and requires review and sign-off by qualified patent counsel before any filing decision.' Plain markdown with the five H2 headings above and nothing else; no JSON envelope for this prompt.
