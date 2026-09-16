# prompts/

The prompt library the runner (`ecosystem/playbook.py` + `ecosystem/prompt_builder.py`) loads and assembles into every agent call. Each file is one playbook prompt (or one runner-owned template) as YAML frontmatter plus a markdown body. The playbook text is kept verbatim from `PLAYBOOK.md` (British spelling retained); only the `[INSERT ...]` placeholders are replaced with "the material embedded under 'Dossier files' below", because the runner embeds the dossier files rather than pasting text.

## File layout

```
prompts/
  _header.md              fragment: the playbook's reusable instruction header (verbatim)
  _contract-drafting.md   fragment: output contract for analysis / drafting / compose / revise kinds
  _contract-review.md     fragment: output contract for the review kind
  README.md               this file
  <id>-<kebab-title>.md   one file per prompt (see the id list below)
```

Fragments start with `_` and carry no frontmatter. Everything else is a prompt file.

## Prompt file format

```markdown
---
id: "07"                       # string. Always quoted so "07" stays "07".
title: "Prior-art claim chart"
phase: P2                      # P1 | P2 | P3 | P4 | P5 | REVIEW | PACKAGE | LOOPBACK
agent: ip-prior-art            # ip-orchestrator | ip-disclosure | ip-prior-art | ip-embodiment | ip-spec | ip-claims | ip-reviewer
kind: analysis                 # analysis | drafting | review | compose | revise | prose
inputs: [intake:prior-art/*, out:P1/01, out:P1/03, terminology, intake:gate-1-answers]
optional_inputs: [out:P1/04, out:P2/06]
deliverable_sections: ["Prior-Art Claim Charts"]   # H3 headings the reply must contain
timeout_s: 1200
include_rationale: false
---
*<one-line purpose from the playbook>*

<playbook prompt text, verbatim>

**How to work.** <prompt-specific guidance: which embedded files to rely on, what to flag, and the output contract reminder>
```

Field semantics:

| Field | Meaning |
|---|---|
| `id` | Playbook number as a string (`"01"` … `"40"`, `"20a"`, `"39a"`, `"40a"`, `"gate14"`) or a runner-template id (`x-...`). Used in session keys, call ids, envelope `prompt`, finding ids and output file names. |
| `title` | Human-readable title (the playbook heading). |
| `phase` | Which phase directory the output is written to. Review prompts are all `REVIEW` regardless of their playbook phase (6, 7 or 8). `39a` and the orchestrator templates are `PACKAGE`. |
| `agent` | The OpenClaw agent the call is addressed to. `x-formal-fix` says `ip-spec` but the runner overrides to `ip-claims` when the corrections target the claims. |
| `kind` | Selects the output contract: `analysis`, `drafting`, `compose` and `revise` use `_contract-drafting.md`; `review` uses `_contract-review.md`; `prose` has no envelope (plain markdown for the human). |
| `inputs` | Dossier material the runner MUST embed; a missing required input is a precondition failure (exit 6). |
| `optional_inputs` | Embedded when present; dropped first when the prompt is over budget. |
| `deliverable_sections` | The H3 headings the reply's `## Deliverable` (drafting kinds) or `## Findings` (review kind) must contain, in order. Empty for `prose`. |
| `timeout_s` | Per-call timeout passed to the transport. |
| `include_rationale` | Whether embedded earlier outputs contribute their `## Rationale` sections. `false` everywhere except `x-gate-brief`; always `false` for the reviewer. |

## Input spec grammar

Only these forms are used:

| Spec | Resolves to |
|---|---|
| `intake:disclosure` | `00-intake/disclosure.md` |
| `intake:prior-art/*` | every file under `00-intake/prior-art/` |
| `intake:priority/*` | every file under `00-intake/priority/` |
| `intake:data/*` | every file under `00-intake/data/` |
| `intake:drawings` | a listing of the file names under `00-intake/drawings/` (names only, no content) |
| `intake:gate-1-answers` | `00-intake/gate-1-answers.md` |
| `out:<PHASE>/<id>` | the latest complete version of that prompt's output (Deliverable + Assumptions + Open Questions; Rationale only when `include_rationale` is true), e.g. `out:P1/04`, `out:P3/11`, `out:P4/20a`, `out:REVIEW/gate14` |
| `out:P4/description` | `04-specification/vN/description.md` (written from `x-p4-compose-description` / `x-p4-revise`) |
| `out:P5/claims` | `05-claims/vN/claims.md` (written from Prompt 25's `### Final Claim Set` / `x-p5-revise`) |
| `confirmed_embodiments` | `03-embodiments/vN/CONFIRMED.md` (written at Gate 2) |
| `terminology` | `terminology/LOCKED.md` (written at Gate 1 from the Prompt 4 table) |
| `registry` | `numerals/registry.json` (written from Prompt 20a) |
| `numeral_crosscheck` | `numerals/cross-check.json` (deterministic, never auto-fixed) |
| `package:description` / `package:figures` / `package:claims` / `package:registry` / `package:abstract` / `package:all` | the rationale-stripped review package under `06-review/vN/package/` |
| `findings:<novelty\|enablement\|design-around\|support\|formal\|all>` | findings of that category from `06-review/vN/findings.json` |
| `corrections` | `corrections[]` from the review envelopes (37, 38, 40a) |
| `disclosure_requests` | `disclosure_requests[]` from the last ip-claims / ip-spec envelope |
| `open_disagreements` | `open_disagreements[]` from the last inner-loop envelopes |
| `inner_loop_flags` | `06-review/vN/inner-loop-flags.md` (written when the inner loop hits its cap) |

## Id list

| id | file | phase | agent | kind |
|---|---|---|---|---|
| 01 | `01-invention-extraction.md` | P1 | ip-disclosure | analysis |
| 02 | `02-commercial-value-map.md` | P1 | ip-disclosure | analysis |
| 03 | `03-inventive-nucleus.md` | P1 | ip-disclosure | analysis |
| 04 | `04-terminology-and-ontology.md` | P1 | ip-disclosure | analysis (Terminology Table: `Preferred term \| Synonyms \| Broader (genus) \| Narrower (species) \| Functional description \| Terms to avoid \| Needs definition`) |
| 05 | `05-inventor-interrogation.md` | P1 | ip-disclosure | analysis |
| 06 | `06-search-concept-decomposition.md` | P2 | ip-prior-art | analysis |
| 07 | `07-prior-art-claim-chart.md` | P2 | ip-prior-art | analysis |
| 08 | `08-novelty-and-inventive-step-attack.md` | P2 | ip-prior-art | analysis |
| 09 | `09-differentiation-matrix.md` | P2 | ip-prior-art | analysis |
| 10 | `10-white-space-analysis.md` | P2 | ip-prior-art | analysis |
| 11 | `11-competitor-design-around-attack.md` | P3 | ip-embodiment | drafting |
| 12 | `12-equivalent-architectures.md` | P3 | ip-embodiment | drafting |
| 13 | `13-parameter-range-ladder.md` | P3 | ip-embodiment | drafting |
| 14 | `14-combination-permutation-map.md` | P3 | ip-embodiment | drafting |
| 15 | `15-failure-mode-embodiments.md` | P3 | ip-embodiment | drafting |
| 16 | `16-specification-blueprint.md` | P4 | ip-spec | drafting |
| 17 | `17-enablement-sufficiency-audit.md` | P4 | ip-spec | analysis |
| 18 | `18-best-implementation-examples.md` | P4 | ip-spec | drafting (Actual vs Prophetic H4s) |
| 19 | `19-definitions-and-broad-language.md` | P4 | ip-spec | drafting |
| 20 | `20-drawings-plan.md` | P4 | ip-spec | drafting |
| 20a | `20a-numeral-registry.md` | P4 | ip-spec | drafting (registry table: `Numeral \| Component \| First figure \| Also appears in`) |
| 21 | `21-claim-family-strategy.md` | P5 | ip-claims | drafting |
| 22 | `22-broad-independent-claims.md` | P5 | ip-claims | drafting |
| 23 | `23-dependent-claim-ladder.md` | P5 | ip-claims | drafting |
| 24 | `24-multiple-independent-formulations.md` | P5 | ip-claims | drafting |
| 25 | `25-claim-differentiation-and-dependency-audit.md` | P5 | ip-claims | drafting (`### Final Claim Set` → `claims.md`) |
| 26 | `26-means-functional-language-audit.md` | P5 | ip-claims | drafting |
| 27 | `27-omnibus-fallback-claim-concepts.md` | P5 | ip-claims | drafting |
| 28 | `28-claim-to-specification-support-matrix.md` | REVIEW | ip-reviewer | review |
| 29 | `29-amendment-basis-stress-test.md` | REVIEW | ip-reviewer | review |
| 30 | `30-us-epo-uk-resilience-review.md` | REVIEW | ip-reviewer | review |
| 31 | `31-priority-entitlement-new-matter-control.md` | REVIEW | ip-reviewer | review (optional `intake:priority/*`) |
| 32 | `32-examiner-red-team.md` | REVIEW | ip-reviewer | review |
| 33 | `33-litigation-enforcement-red-team.md` | REVIEW | ip-reviewer | review |
| 34 | `34-freedom-to-operate-separation.md` | REVIEW | ip-reviewer | review |
| 35 | `35-commercial-moat-score.md` | REVIEW | ip-reviewer | review |
| 36 | `36-final-filing-gap-analysis.md` | REVIEW | ip-reviewer | review |
| 37 | `37-claims-only-forensic-audit.md` | REVIEW | ip-reviewer | review (`package:claims`) |
| 38 | `38-specification-consistency-audit.md` | REVIEW | ip-reviewer | review (`package:description`, `package:figures`, `package:registry`, `terminology`, `numeral_crosscheck`) |
| 39 | `39-filing-checklist.md` | REVIEW | ip-reviewer | review |
| 39a | `39a-abstract.md` | PACKAGE | ip-spec | compose (`package:description`, `package:claims` only) |
| 40a | `40a-formal-compliance.md` | REVIEW | ip-reviewer | review |
| 40 | `40-future-prosecution-map.md` | REVIEW | ip-reviewer | review |
| gate14 | `gate14-filing-gate.md` | REVIEW | ip-reviewer | review (fills `gate14[]`, q 1..14) |
| x-p4-compose-description | `x-p4-compose-description.md` | P4 | ip-spec | compose (`### Description` → `description.md`) |
| x-p4-revise | `x-p4-revise.md` | P4 | ip-spec | revise |
| x-p5-revise | `x-p5-revise.md` | P5 | ip-claims | revise (`### Final Claim Set` → `claims.md`) |
| x-loopback-p3 | `x-loopback-p3.md` | P3 | ip-embodiment | drafting |
| x-formal-fix | `x-formal-fix.md` | REVIEW | ip-spec (or ip-claims, runner override) | revise |
| x-gate-brief | `x-gate-brief.md` | PACKAGE | ip-orchestrator | prose (inputs vary; all listed as optional) |
| x-package-memo | `x-package-memo.md` | PACKAGE | ip-orchestrator | prose |

44 playbook prompts + 7 runner templates = 51 prompt files, plus 3 fragments and this README.

## How the runner assembles a call

`prompt_builder.assemble()` concatenates, in order: `_header.md` → `## Task` (the prompt file body) → `## Dossier files` (each resolved input as `### <path> (v<N>, sha256:<12>)` + fenced content) → `## Locked terminology` (once Gate 1 has locked it) → `## Output contract` (`_contract-drafting.md` or `_contract-review.md` by `kind`; nothing for `prose`). The reply is parsed for the last fenced `json` block; one correction turn is allowed before the call fails.

Review prompts embed only `package:*` material (plus the terminology table and the numeral cross-check for Prompt 38, and the priority document for Prompt 31), so the reviewer never sees a `## Rationale` section, an envelope, or any Phase 1-5 output. Every review body states: "You are reviewing finished text only. You have not been given, and must not ask for, drafting rationale."

## Runner behaviour these files depend on

Seven decisions live in `ecosystem/` rather than in a prompt file. They are recorded here because editing a prompt without knowing them will break a run.

| Behaviour | Where | Why |
|---|---|---|
| **The locked terminology is appended to every prompt once Gate 1 has locked it**, whether or not the prompt lists `terminology` as an input. A prompt that *does* list it gets one copy, under `## Locked terminology`, not two. | `prompt_builder.assemble` | The lock binds every agent from Gate 1 onwards, and `terminology.check_output` polices drafting output against it. A prompt whose input list forgot to ask would be checked against a table it was never shown. |
| **A prompt may read its own phase's in-progress version**; nothing outside that phase can. | `dossier.latest_output(..., include_running=)` | Prompt 02 reads Prompt 01 inside one Phase 1 entry. From outside, an unfinished version does not exist, so no later phase drafts from material a gate has not seen. |
| **`x-formal-fix` output is written to the phase that owns the corrected text** (`P5` for claims, `P4` for description), in a new version with `trigger: formal-fix`, and `### Corrected Text` replaces `claims.md` / `description.md` there. Its `agent` frontmatter is overridden to `ip-claims` or `ip-spec` by target. | `runner._formal_fix` | The file says `phase: REVIEW`, but a claims correction is claims work. Writing it to REVIEW would put filing text in the reviewer's directory and leave `claims.md` uncorrected. |
| **The 37/38/40a re-run after a formal fix is stored as `37r` / `38r` / `40ar`** (call slot `recheck`). A `<id>r` envelope supersedes `<id>` when findings are aggregated. | `runner._formal_pass`, `loopback._envelope_paths` | The originals stay readable, and corrections that have just been applied do not reappear in `findings.json`. |
| **Prompt 26 re-runs in inner-loop round 2+ when `x-p5-revise` mentions functional language** in `open_disagreements` or `support_refs` (case-insensitive substring `functional`). | `runner._functional_flag` | The envelope has no field for "I used means-plus-function language", so the runner reads the words the drafter used. A false positive costs one cheap audit; a false negative costs claim scope. |
| **Convergence uses the union of `disclosure_requests` across the current P5 version's envelopes and `open_disagreements` across the current P4 version's**, not only the last envelope. | `runner._signals` | A request raised by Prompt 25 and ignored by Prompt 26 is still unmet. |
| **Gate 1 always writes `00-intake/gate-1-answers.md`.** With `--answers-file` it is the human's file; without, it is a generated list of Phase 1's open questions, each marked *not answered*. | `gates._apply_gate1` | Prompts 06-15 declare `intake:gate-1-answers` as required. Accepting Gate 1 without answering anything is legitimate, so the file says so plainly rather than leaving the agents to infer it from a missing input. |

## Editing rules

- Keep the playbook sentence verbatim; put runner-specific guidance in the **How to work** paragraph.
- Changing `deliverable_sections` changes what the contract parser enforces and, for 25, `x-p5-revise`, `x-p4-compose-description`, `x-p4-revise` and `x-formal-fix`, what the runner extracts into the package files. Change those together with the runner.
- The Prompt 4 and Prompt 20a table column headers are parsed by `terminology.py` and `numerals.py`; do not reword them.
