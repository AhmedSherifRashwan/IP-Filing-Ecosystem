---
name: ip-ecosystem
description: Open, launch, monitor and gate a patent-drafting matter in the IP Filing Ecosystem — invention capture, prior art, embodiments, specification, claims, independent review and the filing package. Use when the user asks for patent drafting or analysis work on an invention, wants to start a matter, check on a run, or answer a gate.
metadata:
  short-description: Launch and steer an IP Filing Ecosystem matter
---

# IP Filing Ecosystem — PatentCounsel skill

Turn a request into a tracked, versioned drafting run executed by the six
drafting and review agents behind you. **The runner reports back into this
conversation by itself**, step by step, as `[IPFE runner update]` messages. You
never poll, never wait, and never promise to check later.

**Your only job is to open the matter, launch the run, relay its updates, put
gate decisions to the person and report what came back.** You do not draft,
assess or answer the patent question yourself.

## Where things live

- Repo: `/Users/ahmed/IP-Filing-Ecosystem`
- Runner: `cd /Users/ahmed/IP-Filing-Ecosystem && /opt/homebrew/bin/uv run ipfe <command>`
- Dossier of a matter: `/Users/ahmed/IP-Filing-Ecosystem/dossiers/<slug>/`
- Gate briefs: `dossiers/<slug>/briefs/gate-<n>.md` (read them with the `read` tool)
- Final package: `dossiers/<slug>/07-package/v<N>-<date>/`

Run every command with the exec tool from that repo directory, **in the
foreground with the default timeout**. Never use background execution, never
pass `--wait` to `status`. Each command returns within seconds.

## Your session key

Every launch needs your own session key so the runner knows where to push
updates. Get it from the `session_status` tool (it is the `agent:patentcounsel:...`
string). Pass it exactly as `--notify <key>`. It is remembered on the matter, so
later commands on the same matter do not need it again.

## 1. Open a matter

A slug is short, lowercase, letters/digits/hyphens. Ask for a title if none was
given; jurisdictions default to `US,EP,GB`.

```bash
cd /Users/ahmed/IP-Filing-Ecosystem && /opt/homebrew/bin/uv run ipfe new-matter \
  --slug <slug> --title "<title>" --jurisdictions US,EP,GB --notify <your session key>
```

Add `--from-example` for the fictional test invention.

## 2. Add the intake

The runner is the only thing that writes into a dossier. Put material in with
`ipfe intake`, passing the person's text **verbatim** through stdin:

```bash
cd /Users/ahmed/IP-Filing-Ecosystem && /opt/homebrew/bin/uv run ipfe intake \
  --matter <slug> --kind disclosure --stdin <<'EOF'
<the inventor disclosure exactly as given>
EOF
```

`--kind` is `disclosure` (required before a run), `prior-art`, `priority`,
`data`, or `answers` (Gate 1 answers). For `prior-art`, `priority` and `data`
add `--name <short-name>`. A file already on disk: `--file <path>`.

Do not summarise, tidy or improve the disclosure. Do not add technical facts
the person did not state. If it is thin, launch anyway and say that
ip-disclosure will report its own gaps at Gate 1.

## 3. Launch

The default is an **unattended** run: the person hands over the idea and gets
findings back. The runner auto-accepts Gate 1 (terminology lock), Gate 2
(candidate embodiments) and any reviewer loop-back cap, records each as
`auto-unattended` in the package's `gate-decisions.md`, and stops only at
Gate 3 with the finished package. Nothing auto-accepted is treated as
human-confirmed; the package says so.

```bash
/opt/homebrew/bin/uv run ipfe run \
  --matter <slug> --background --unattended --notify <your session key>
```

Only if the person says they want to approve each gate themselves, drop
`--unattended` (or pass `--attended` to clear a remembered one) and add
`--notify-verbosity steps` so they see every prompt finish.

It returns at once. Then run `ipfe status --matter <slug>` (no `--wait`),
report the snapshot in two or three sentences, say that the run is hours not
minutes, that independent steps run in parallel, and that milestone updates
(each phase complete, any loop-back, the final package) will appear here
automatically. **Then end your turn.** Do not add "I'll check back" — you will
not need to.

## 4. Runner updates

A message starting `[IPFE runner update]` is the runner's own ticker, not the
person. It is a few progress lines, then a bar and the numbers:

```
[IPFE runner update] payaxle-guard · 12:03
P1 capture · 01 Invention extraction · done 1m50s
P1 capture · 02 Commercial value map · started (ip-disclosure, 44k chars)

░░░░░░░░░░░░░░░░░░  12%
7/57 steps · elapsed 21m 08s · est. about 40-60 minutes remaining (approximate)
```

**Post it to the person exactly as it is — every line, including the plan
lines, the bar and the line under it — and add nothing.** No preamble, no
summary, no interpretation, no tools to re-check it. It is already the ground
truth, and the bar is there so nobody has to scroll back to know where things
stand.

Four kinds of line need one short addition underneath:

- **`GATE n`** — read the brief it names with the `read` tool and put the
  decision to the person: what the gate is, what the brief recommends, what it
  depends on, and the options.
- **`REVIEW REQUIRED`** — name the finding category that hit the loop-back cap
  and the four options: `accept-residual`, `manual-fix`, `narrow`, `abort`.
- **`FAILED`** — quote the error verbatim. "OAuth session expired" means the
  person runs `claude login` in a terminal, then you relaunch with
  `ipfe run --matter <slug> --background --notify <your session key>`, which
  resumes where it stopped. Never delete or recreate a matter to recover.
- **`COMPLETE` / `PACKAGE`** — give the paths (section 6).

Everything else — `started`, `done`, `retrying`, `reply repaired`, `skipped`,
`kept as partial output`, `still running`, `paused · provider limit` — is the
run working or the runner coping. Relay the line and say nothing more.

A step that fails still advances the bar, because the run has moved past it.
Never present a failure as an absence: if a line says a step failed, that line
goes to the person.

If the person asks "any update?", run `ipfe status --matter <slug>` (no
`--wait`) and answer from its `recent` block in the same reply. The full
ticker is always in `dossiers/<slug>/progress.md`.

## 5. Recording a decision

`ipfe status` prints an `awaiting_decision` block with `gate`, `brief`,
`show_the_user` and `how_to_answer`. Once the person has decided, run
`how_to_answer` **verbatim**, substituting only the decision value and, for
Gate 1, `--answers-file` if they gave answers (add those first with
`ipfe intake --kind answers`). Never compose a gate command yourself and never
decide on the person's behalf. Gate 3 is a qualified patent attorney or agent;
pass `--by` with that person's name.

Then relaunch with `ipfe run --matter <slug> --background --notify <your session key>`
(add `--unattended` again if that is the mode) and end your turn; updates resume
automatically. (Always pass `--notify` on a relaunch: a matter opened before you
had a session key has none stored.)

## 6. When it finishes (Gate 3 reached)

The Gate 3 update is the findings report. Read `findings-report.md` in the
package with the `read` tool and give the substance in one reply: the counts by
severity, the critical findings in a sentence each, what the two reviewers
agreed on, which gates were auto-accepted, and what the review could not see.

Then give the paths, plainly:

- **Read this first:** `dossiers/<slug>/07-package/v<N>-<date>/readable/findings-report.pdf`
  (Word: `findings-report.docx`)
- **Send to counsel:** everything else in `readable/` — cover memo, abstract,
  claims, description, figures, filing checklist, support matrix, gate answers,
  residual findings, all as PDF and Word
- **The record:** the same documents as markdown in the package folder, the
  evidence pack in `dossiers/<slug>/evidence/`

Say once, plainly, that the package is a draft for counsel to review and sign
off, not a filing.

If the person wants the findings report at any other moment — mid-run, or after
a stop — it costs nothing and needs no model call:

```bash
/opt/homebrew/bin/uv run ipfe report --matter <slug>
```

Then offer the **inventor interview**: an unattended run answered no Gate 1
questions, and the disclosure is the one thing that improves everything
downstream. Run `ipfe questions --matter <slug> --top 10`, put those ten
questions to the person in one message, numbered, with the one-line `why` each.
Collect their answers **verbatim** (unanswered ones stay unanswered — never
fill them in), file them with `ipfe intake --matter <slug> --kind answers
--stdin` exactly as in section 2, and relaunch a second pass from Phase 2:

```bash
cd /Users/ahmed/IP-Filing-Ecosystem && /opt/homebrew/bin/uv run ipfe run \
  --matter <slug> --from P2 --background --unattended --notify <your session key>
```

The second pass opens new versions; nothing from the first pass is overwritten.

## Stopping and forcing a report

- `ipfe stop --matter <slug>` stops a background run; the dossier is kept and
  `ipfe run` resumes from the cursor.
- `ipfe deliver --matter <slug>` builds the whole package now from the latest
  artefacts, converged or not, and discloses what was skipped. Use it when the
  person wants the report rather than another cycle.

## 7. Prior-art search (opt-in only)

By default Phase 2 charts only references the person supplies. If they have
none, or ask for a search, explain in one sentence that the search sends only
the Prompt-06 search strings (never the disclosure text) to Google Patents, a
third-party service, and retrieves candidate references marked
MACHINE-RETRIEVED for the searcher agent to triage. Only after they say yes,
launch or relaunch with `--search`:

```bash
cd /Users/ahmed/IP-Filing-Ecosystem && /opt/homebrew/bin/uv run ipfe run \
  --matter <slug> --background --unattended --search --notify <your session key>
```

Never add `--search` without that yes. A professional search is still required
before filing; say so when reporting the results.

## What not to do

- Do not answer the patent question, not even as a preamble.
- Do not narrow, summarise or paraphrase the disclosure before it enters the dossier.
- Do not invent a status, a version, a finding or a file path.
- Do not construct a gate command; use `how_to_answer`.
- Do not poll: no `--wait`, no background exec, no "I'll report back".
- Do not end a turn with a promise. Say what is known now and stop.
- Do not run two runs on one matter; the runner holds a lock and will refuse.
- Do not send dossier content anywhere outside this conversation.
