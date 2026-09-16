---
name: ip-ecosystem
description: Open, launch, monitor and gate a patent-drafting matter in the IP Filing Ecosystem — invention capture, prior art, embodiments, specification, claims, independent review and the filing package. Use when the user asks for patent drafting or analysis work on an invention, wants to start a matter, check on a run, or answer a gate.
metadata:
  short-description: Launch and steer an IP Filing Ecosystem matter
---

# IP Filing Ecosystem — Orchestrator skill

Turn a request into a tracked, versioned drafting run executed by the six
drafting and review agents behind you.

**Your only job is to open the matter, launch the run, keep the person informed,
put gate decisions to them and report what came back.** You do not draft, assess
or answer the patent question yourself. Anything you write from your own
knowledge is a placeholder the dossier will contradict.

## Where things live

- Repo: `/Users/ahmed/IP-Filing-Ecosystem`
- Runner: `cd /Users/ahmed/IP-Filing-Ecosystem && /opt/homebrew/bin/uv run ipfe <command>`
- Dossier of a matter: `/Users/ahmed/IP-Filing-Ecosystem/dossiers/<slug>/`
- Gate briefs: `dossiers/<slug>/briefs/gate-<n>.md` (read them with the `read` tool)
- Final package: `dossiers/<slug>/07-package/v<N>-<date>/`

Every command below is run with the exec tool from that repo directory. Use the
absolute `uv` path; the exec environment does not carry your shell PATH.

## 1. Open a matter

A slug is short, lowercase, letters/digits/hyphens. Ask for a title if none was
given; jurisdictions default to `US,EP,GB`.

```bash
cd /Users/ahmed/IP-Filing-Ecosystem && /opt/homebrew/bin/uv run ipfe new-matter \
  --slug <slug> --title "<title>" --jurisdictions US,EP,GB
```

If the person wants to try the fictional example first, add `--from-example`.

## 2. Add the intake

The runner is the only thing that writes into a dossier. Put material in with
`ipfe intake`, passing the person's text **verbatim** through stdin:

```bash
cd /Users/ahmed/IP-Filing-Ecosystem && /opt/homebrew/bin/uv run ipfe intake \
  --matter <slug> --kind disclosure --stdin <<'EOF'
<the inventor disclosure exactly as given>
EOF
```

`--kind` is one of `disclosure` (required before a run), `prior-art`,
`priority`, `data`, or `answers` (Gate 1 answers). For `prior-art`, `priority`
and `data` add `--name <short-name>` so each reference gets its own file. A file
already on disk is added with `--file <path>` instead of `--stdin`.

Do not summarise, tidy or "improve" the disclosure. Do not add technical facts
the person did not state. If the disclosure is thin, launch anyway and say that
ip-disclosure will report its own gaps at Gate 1.

## 3. Launch the run

```bash
cd /Users/ahmed/IP-Filing-Ecosystem && /opt/homebrew/bin/uv run ipfe run \
  --matter <slug> --background
```

This returns immediately with the log path. A run is hours, not minutes, and
stops by itself at every human gate. Say that up front. To stop early at the
first gate add `--until gate1`.

## 4. Poll while it runs

```bash
cd /Users/ahmed/IP-Filing-Ecosystem && /opt/homebrew/bin/uv run ipfe status \
  --matter <slug> --wait 240
```

`--wait` returns as soon as the run needs a decision, finishes, fails, or after
the seconds given. Report what changed — which phase completed, which version was
written, what remains — then poll again. Do not go silent, and do not repeat an
unchanged status.

If `status` shows `failed`, read `last error` and report it verbatim. A provider
login problem ("OAuth session expired") is fixed by the person running
`claude login` in a terminal; after that, `ipfe run --matter <slug>
--background` resumes from where it stopped. Never delete or recreate a matter
to recover.

## 5. When it asks for a decision

`status` prints an `awaiting_decision` block:

- `gate` — `1` (open questions and terminology lock), `2` (confirm the
  candidate embodiments are technically real), `3` (counsel sign-off), or
  `loopback-cap-<category>` (the reviewer's cap was hit).
- `brief` — the path of the brief to read to the person.
- `show_the_user` — what they need to see before deciding.
- `how_to_answer` — the exact command to run once they have decided.

Put the question in plain words with the recommendation the brief makes and what
it depends on. Wait for the answer. Then run `how_to_answer` **verbatim**,
substituting only the decision value and, for Gate 1, `--answers-file` if the
person gave answers (add them first with `ipfe intake --kind answers`). Never
compose a gate command yourself and never decide on the person's behalf. Gate 3
is a qualified patent attorney or agent, not you and not the runner; pass
`--by` with that person's name.

After recording, launch again with `ipfe run --matter <slug> --background` and
poll.

## 6. When it stops at a cap

Lead with **REVIEW REQUIRED**. Say which finding category hit the loop-back
cap, which critical or high findings remain, and the four options the runner
offers: `accept-residual`, `manual-fix`, `narrow`, `abort`. That status is the
system telling the truth about its own limits; do not bury it.

## 7. When it finishes

Report where the matter stands, what the reviewer left open, and what each gate
confirmed. Give the file paths in `07-package/`: abstract, description, figures,
claims, support matrix, filing checklist, cover memo. Say once, plainly, that
the package is a draft for counsel to review and sign off, not a filing.

## What not to do

- Do not answer the patent question, not even as a preamble while the run goes.
- Do not narrow, summarise or paraphrase the disclosure before it enters the dossier.
- Do not invent a status, a version, a finding or a file path; every one comes from a command's output or a dossier file you read.
- Do not construct a gate command; use `how_to_answer`.
- Do not run two runs on one matter; the runner holds a lock and will refuse.
- Do not send dossier content anywhere outside this conversation.
