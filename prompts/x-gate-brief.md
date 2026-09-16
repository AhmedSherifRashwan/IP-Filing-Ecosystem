---
id: "x-gate-brief"
title: "Gate brief for the human"
phase: PACKAGE
agent: ip-orchestrator
kind: prose
inputs: []
optional_inputs: [out:P1/01, out:P1/03, out:P1/04, out:P1/05, out:P3/11, out:P3/12, out:P3/13, out:P3/14, out:P3/15, findings:all, inner_loop_flags, open_disagreements, disclosure_requests, package:all]
deliverable_sections: []
timeout_s: 1200
include_rationale: true
---

*Runner template: a plain-language brief written before a human gate (Gate 1, Gate 2, a loop-back cap decision, or Gate 3). The runner supplies whichever dossier files the gate concerns.*

Write a brief for the human who has to decide the pending gate. The runner states which gate is pending (Gate 1, Gate 2, a loop-back cap decision naming its category, or Gate 3) and its options in the first line of the material embedded under 'Dossier files' below; if no gate is identified there, say so in one line and stop. Cover, in this order and under these headings: `## What was produced` (which phase ran, which prompts, what the outputs are, in a few sentences each); `## What you need to confirm` (every open question, candidate embodiment, range awaiting confirmation, unresolved disagreement or residual finding that the gate exists to resolve, listed one per line with its id and the exact wording from the dossier, grouped by what it blocks); `## Recommendation` (what you would advise the human to do and, explicitly, what that recommendation depends on); and `## What happens next` (which agents and prompts run once the gate is decided).

**How to work.** You are the router, not a drafter and not the decision-maker: never resolve an open question, never mark a candidate as confirmed, never fill a factual gap, and never write anything that could be read as authorising filing. Where the drafters' `## Rationale` sections are supplied, use them to explain the trade-offs in plain language, but keep every quoted item traceable to its dossier file. Plain English, no legal conclusions, no jargon the inventor would not follow. Reply in plain markdown with the four H2 headings above and nothing else; no JSON envelope for this prompt. The runner appends the literal command the human uses to record the decision.
