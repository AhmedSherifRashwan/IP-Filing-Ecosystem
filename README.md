# IP Filing Ecosystem

A script-driven, seven-agent patent drafting ecosystem that executes the
[Patent Drafting Prompt Playbook](PLAYBOOK.md) on OpenClaw.

Seven OpenClaw agents, each with its own workspace and `SOUL.md`, do the
drafting and review. A small Python runner (`ipfe`) sequences them through the
playbook's eight phases, enforces the three mandatory human gates, the
inner-loop and loop-back caps, the terminology lock, and the independence of the
reviewer. State lives in a versioned, gitignored dossier per matter. Nothing is
treated as fact until a human gate accepts it, and no agent can authorise filing.

> AI drafting is a drafting and analysis aid, not a substitute for
> jurisdiction-specific advice from a qualified patent attorney or patent agent.

## The seven agents

| Agent id | Playbook role | Prompts | Model |
|---|---|---|---|
| `ip-orchestrator` | Agent 1 — front door and coordinator (router, not drafter) | process | anthropic/claude-sonnet-5 |
| `ip-disclosure` | Agent 2 — Disclosure Analyst | 1–5 | anthropic/claude-opus-5 |
| `ip-prior-art` | Agent 3 — Prior Art & Differentiation Strategist | 6–10 | anthropic/claude-opus-5 |
| `ip-embodiment` | Agent 4 — Embodiment & Design-Around Architect | 11–15 | anthropic/claude-opus-5 |
| `ip-spec` | Agent 5 — Specification, Drawings & Abstract Drafter | 16–20a, 39a | anthropic/claude-opus-5 |
| `ip-claims` | Agent 6 — Claims Architect | 21–27 | anthropic/claude-opus-5 |
| `ip-reviewer` | Agent 7 — Independent Reviewer (blind to rationale) | 28–40a, 14-question gate | openai/gpt-5.6-sol |

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

## Run a matter

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

Pipeline: Phase 1 → **Gate 1** (open questions, terminology lock) → Phase 2 →
Phase 3 → **Gate 2** (confirm embodiments are real) → Phases 4↔5 inner loop
(cap 3) → independent review → loop-backs (cap 2 per finding category) →
abstract, filing checklist, 14-question gate → final package in
`dossiers/<slug>/07-package/` → **Gate 3** (counsel sign-off).

## Develop

```bash
uv run pytest -q
```

```bash
uv run ruff check ecosystem tests && uv run mypy ecosystem
```

Prompt-file format and the dossier input grammar are documented in
[prompts/README.md](prompts/README.md).
