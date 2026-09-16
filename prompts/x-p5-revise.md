---
id: "x-p5-revise"
title: "Revise the claim set"
phase: P5
agent: ip-claims
kind: revise
inputs: [out:P5/claims, out:P4/description, registry, terminology]
optional_inputs: [findings:all, corrections, disclosure_requests, open_disagreements]
deliverable_sections: ["Final Claim Set", "Change Log"]
timeout_s: 1800
include_rationale: false
---

*Runner template: inner-loop round 2+ and loop-back re-entry into Phase 5. Revise, do not restart.*

Take the role of both prosecution counsel and a competitor. Starting from the current claim set embedded below, and reading the revised Description embedded below as the sole source of support, refine the claims to respond to the findings, corrections and resolved disclosure requests supplied, while maximising commercially meaningful scope and retaining support and a credible distinction over the art the findings describe. Do not solve prior-art problems by reflexively adding narrow details. First test whether a broader relationship, architecture, sequence or technical effect is supportable in the revised Description. Maintain at least one meaningful fallback ladder under every independent claim. For every revision, provide: old wording, new wording, reason, scope impact, prior-art impact, design-around impact and exact specification support.

**How to work.** Revise only what a finding, correction, or newly available disclosure justifies; claims with no trigger stay word-for-word unchanged. A limitation still lacking an explicit passage in the revised Description is not drafted and goes back into `disclosure_requests`. Under `### Final Claim Set` return the complete claim set as numbered claims in plain text, one claim per paragraph, nothing else (the runner replaces `claims.md` with it verbatim); renumber and fix dependencies if claims were added or removed. Under `### Change Log` give one entry per revision with exactly these fields: claim | old wording (quoted) | new wording (quoted) | reason (finding id / correction / disclosure request) | scope impact | prior-art impact | design-around impact | exact support (paragraph numbers). If a finding asked for narrowing you consider unnecessary, say so in `open_disagreements` and leave the claim broad; the human decides. Use locked terms and registry component names verbatim. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
