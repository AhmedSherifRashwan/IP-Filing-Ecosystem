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

A message starting `[IPFE runner update]` is the runner itself, not the person.
It is ground truth at that moment. Relay it in one to three plain sentences
(which phase, which prompt, how long, what is next) and do not run tools to
re-verify it. Three cases need more than a relay:

- **Gate reached** — read the brief it names with the `read` tool, and in the
  same reply put the decision to the person: what the gate is, the
  recommendation the brief makes, what that depends on, and the options. In an
  unattended run the only gate you will see is Gate 3: its brief is the
  findings report. Give the person the substance — what the reviewer found,
  what remains open, which gates were auto-accepted — and the package paths.
- **Failure** — quote the error verbatim. "OAuth session expired" means the
  person must run `claude login` in a terminal; then relaunch with
  `ipfe run --matter <slug> --background --notify <your session key>`, which
  resumes where it stopped.
  Never delete or recreate a matter to recover.
- **REVIEW REQUIRED / loop-back cap** — lead with that phrase, say which
  finding category hit the cap and the four options: `accept-residual`,
  `manual-fix`, `narrow`, `abort`.

If the person asks "any update?", run `ipfe status --matter <slug>` (no
`--wait`) and answer from its output in the same reply.

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

## 6. When it finishes

Report where the matter stands, what the reviewer left open, and what each
gate confirmed. Give the file paths in `07-package/`: abstract, description,
figures, claims, support matrix, filing checklist, cover memo. Say once, plainly,
that the package is a draft for counsel to review and sign off, not a filing.

## What not to do

- Do not answer the patent question, not even as a preamble.
- Do not narrow, summarise or paraphrase the disclosure before it enters the dossier.
- Do not invent a status, a version, a finding or a file path.
- Do not construct a gate command; use `how_to_answer`.
- Do not poll: no `--wait`, no background exec, no "I'll report back".
- Do not end a turn with a promise. Say what is known now and stop.
- Do not run two runs on one matter; the runner holds a lock and will refuse.
- Do not send dossier content anywhere outside this conversation.
