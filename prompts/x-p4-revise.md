---
id: "x-p4-revise"
title: "Revise the Description"
phase: P4
agent: ip-spec
kind: revise
inputs: [out:P4/description, registry, terminology, disclosure_requests, confirmed_embodiments]
optional_inputs: [findings:all, corrections, open_disagreements, out:P4/17]
deliverable_sections: ["Description", "Change Log"]
timeout_s: 5400
include_rationale: false
---

*Runner template: inner-loop round 2+ and loop-back re-entry into Phase 4. Revise, do not restart.*

Revise the current Description embedded below so that it satisfies each disclosure request from the claims drafter and each finding routed to Phase 4, by ADDING disclosure that is already within the inventors' conception as recorded in the confirmed embodiments (`CONFIRMED.md`). Never narrow the described scope to fit a claim or to answer a finding; if a request or finding can only be satisfied by narrowing or by matter the confirmed embodiments do not contain, leave the text unchanged on that point and report why. Keep every existing paragraph number stable; insert new paragraphs with the next free numbers at the end of the relevant section or as lettered sub-numbers ([0042a]) where the jurisdiction convention allows, and say which convention you used. Keep every reference numeral consistent with the registry; if a newly described component needs a numeral, use the next free numeral in the appropriate series and list it so the registry can be updated. Then list every change.

**How to work.** Work request by request and finding by finding. Under `### Description` return the complete revised Description (the runner replaces `description.md` with this section verbatim, so it must be whole, numbered and free of notes). Under `### Change Log` give a table: change id | trigger (disclosure request text, finding id, correction, or open disagreement) | location (paragraph numbers) | change (added / amended / not changed) | text added or amended (quoted) | basis in `CONFIRMED.md` (which confirmed embodiment) | note. A request you could not satisfy from confirmed material stays in `disclosure_requests` in the envelope, re-addressed to the human, with the reason; a disagreement with the claims drafter you still hold goes in `open_disagreements`. Use locked terms verbatim. Reply exactly in the drafting output contract: `## Deliverable` (with the H3 sections named above), `## Rationale`, `## Assumptions`, `## Open Questions`, then one final fenced `json` envelope. No preamble before `## Deliverable`; nothing after the envelope. Reuse locked preferred terms verbatim; declare any other term in `new_terms` with a reason.
