# IP Filing Ecosystem

A script-driven, seven-agent patent drafting ecosystem that executes the
[Patent Drafting Prompt Playbook](PLAYBOOK.md) on OpenClaw.

Nine OpenClaw agents (the seven playbook roles, a second independent reviewer
and an opt-in prior-art searcher), each with its own workspace and `SOUL.md`,
do the drafting and review. You talk to **PatentCounsel** (`patentcounsel`),
the front door, inside OpenClaw; it drives everything and pushes progress back
into your chat. A small Python runner (`ipfe`) sequences them through the
playbook's eight phases, enforces the three mandatory human gates, the
inner-loop and loop-back caps, the terminology lock, and the independence of the
reviewer. State lives in a versioned, gitignored dossier per matter. Nothing is
treated as fact until a human gate accepts it, and no agent can authorise filing.

> AI drafting is a drafting and analysis aid, not a substitute for
> jurisdiction-specific advice from a qualified patent attorney or patent agent.

## The seven agents

| Agent id | Playbook role | Prompts | Model |
|---|---|---|---|
| `patentcounsel` (**PatentCounsel**) | Agent 1 — front door and coordinator (router, not drafter) | process | anthropic/claude-sonnet-5 |
| `ip-disclosure` | Agent 2 — Disclosure Analyst | 1–5 | anthropic/claude-opus-5 |
| `ip-prior-art` | Agent 3 — Prior Art & Differentiation Strategist | 6–10 | anthropic/claude-opus-5 |
| `ip-embodiment` | Agent 4 — Embodiment & Design-Around Architect | 11–15 | anthropic/claude-opus-5 |
| `ip-spec` | Agent 5 — Specification, Drawings & Abstract Drafter | 16–20a, 39a | anthropic/claude-opus-5 |
| `ip-claims` | Agent 6 — Claims Architect | 21–27 | anthropic/claude-opus-5 |
| `ip-reviewer` | Agent 7 — Independent Reviewer (blind to rationale) | 28–40a, 14-question gate | openai/gpt-5.6-sol |
| `ip-reviewer-b` | Second independent reviewer, different model family; consensus computed by the runner | 28–38, 40a | anthropic/claude-opus-5 |
| `ip-searcher` | Prior-art candidate triage (opt-in search only) | x-search-triage | anthropic/claude-sonnet-5 |

Each agent's workspace files live in `agents/<id>/` (`SOUL.md`, `AGENTS.md`,
`IDENTITY.md`) plus the shared `agents/_shared/USER.md`. The install script
copies them to `~/.openclaw/workspaces/<id>/`. Agents have `tools.profile:
minimal` and no agent-to-agent messaging: the runner does all routing, which is
what keeps the reviewer blind by construction.

## Layout

```
PLAYBOOK.md              the playbook (source of every prompt)
agents/                  seven agent workspaces + shared USER.md
prompts/                 one file per playbook prompt (01–40a, gate14) + runner templates (x-*)
ecosystem/               the runner: dossier, state, prompt assembly, transport, gates, loop-back, CLI
openclaw/                config patch registering the seven agents
scripts/install.sh       idempotent install (workspaces, config patch, gateway restart)
tests/                   unit + replay tests; tests/fixtures/example-matter is a fictional invention
dossiers/<slug>/         one versioned dossier per matter (gitignored, confidential)
```

## Install

Requirements: OpenClaw 2026.9.x at `~/.openclaw`, `uv`.

```bash
scripts/install.sh --check
```

```bash
scripts/install.sh
```

The script enforces the bootstrap size caps, copies only changed workspace
files, backs up `~/.openclaw/openclaw.json`, applies
`openclaw/openclaw.patch.json5` with `openclaw config patch`, validates, and
restarts the gateway.

## Talk to it in OpenClaw

```bash
openclaw tui agent:patentcounsel:main
```

Hand PatentCounsel the invention. By default it runs the matter **unattended**:
Gates 1 and 2 and any reviewer loop-back cap are auto-accepted (recorded as
`auto-unattended` in the package's `gate-decisions.md`), only Gate 3 (counsel
sign-off) waits for a human, and milestone updates are pushed into the chat by
the runner itself. Afterwards it offers the ten highest-leverage inventor
questions and a second pass from Phase 2 with your answers. Prior-art search
against Google Patents is opt-in (`--search`); only Prompt-06 search strings
leave the machine, never the disclosure.

## Run a matter from the shell

```bash
uv run ipfe new-matter --slug demo --title "Self-purging valve" --jurisdictions US,EP,GB --from-example
```

Put the real disclosure in `dossiers/<slug>/00-intake/disclosure.md` and any
prior-art references (as text) under `00-intake/prior-art/`. Then:

```bash
uv run ipfe run --matter demo --until gate1 --dry-run
```

```bash
uv run ipfe run --matter demo
```

```bash
uv run ipfe status --matter demo --wait 900
```

The run stops at every human gate (exit code 10) and at a loop-back cap
(exit 11). `status` prints an `awaiting_decision` block with a literal
`how_to_answer` command; run it verbatim, then `run` again.

Useful flags on `run`: `--unattended`, `--search` (opt-in prior art), `--background`,
`--notify <openclaw session key>` (push updates), `--from P2` (second pass).
Other commands: `ipfe questions` (ranked inventor questions), `ipfe evidence`
(chain-of-custody pack, `--verify` to re-check hashes), `ipfe verify-support`
(deterministic claim-support check), `ipfe intake`.

Pipeline: Phase 1 → **Gate 1** (open questions, terminology lock) → Phase 2 →
Phase 3 → **Gate 2** (confirm embodiments are real) → Phases 4↔5 inner loop
(cap 3) → independent review → loop-backs (cap 2 per finding category) →
abstract, filing checklist, 14-question gate → final package in
`dossiers/<slug>/07-package/` + evidence pack in `dossiers/<slug>/evidence/`
(hashes of every input, prompt, reply and output; per-call provenance; root
hash anchored in the committed `evidence-ledger/`) → **Gate 3** (counsel
sign-off).

The independent review runs two reviewers on different model families in
parallel; findings both raise are marked consensus, and only consensus or
critical findings drive loop-backs.

## Model tiering

Analysis-only prompts (commercial value map, inventor questions, all of Phase
2, parameter ladders, combination map, failure modes, enablement audit, search
triage) run on `openai/gpt-5.6-sol` through the OpenClaw OpenAI login, so the
Claude session quota is spent on the prompts where wording matters: invention
capture and terminology, design-arounds and equivalents, the specification,
the claims and every revision. Tiered calls go through `openclaw agent --model`
(the gateway RPC refuses per-call overrides). Configure with
`IPFE_MODEL_ANALYSIS=<model id>` (`none` disables) and
`IPFE_ANALYSIS_PROMPTS=02,05,...` to change the set; a `model:` line in a
prompt's frontmatter overrides both. The model actually used is recorded per
call in the evidence pack's provenance.

## Develop

```bash
uv run pytest -q
```

```bash
uv run ruff check ecosystem tests && uv run mypy ecosystem
```

Prompt-file format and the dossier input grammar are documented in
[prompts/README.md](prompts/README.md).
