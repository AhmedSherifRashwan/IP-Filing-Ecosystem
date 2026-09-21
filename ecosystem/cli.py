"""`ipfe` - the command line. Every operator action the pipeline supports.

Two design rules, both borrowed from the Nucleus house pattern and both about
the human:

* **Exit codes are the API.** `10` means a human gate is waiting, `11` a
  loop-back cap, `12` that `--until` did its job. A script that wraps this can
  branch without parsing prose (plan s3.6).
* **`status` prints the literal next command.** When a gate is pending it emits
  `how_to_answer` as a ready-to-run `record-decision` line, so nobody has to
  reconstruct the flags from documentation while a run is blocked.

Nothing here talks to OpenClaw except `check` (read-only health) and `run`
(through `transport`), and `IPFE_TRANSPORT=fake:<dir>` swaps the transport for
the replay fake, which is how the end-to-end tests exercise this file.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Final

from ecosystem import evidence as evidence_module
from ecosystem import gates as gates_module
from ecosystem import ledger as ledger_module
from ecosystem import loopback, package
from ecosystem import questions as questions_module
from ecosystem import state as state_module
from ecosystem import support_check as support_module
from ecosystem.calls import ContractFailed, TerminologyBlocked
from ecosystem.config import AGENT_IDS, PHASES, Settings
from ecosystem.dossier import Dossier, DossierError, InputContext, MissingInput
from ecosystem.log import EventLog
from ecosystem.notify import SessionNotifier
from ecosystem.playbook import PlaybookError, load_playbook
from ecosystem.prompt_builder import PromptTooLarge, assemble
from ecosystem.runner import Runner, StopReason
from ecosystem.state import Lock, LockHeld, MatterState, StateError
from ecosystem.terminology import LockedTable, check_output
from ecosystem.transport import AutoTransport, GatewayError, Transport

log = logging.getLogger("ipfe")

__all__ = ["EXIT", "build_parser", "main"]

EXIT: Final[dict[str, int]] = {
    "ok": 0,
    "gate": 10,
    "cap": 11,
    "until": 12,
    "usage": 2,
    "transport": 3,
    "contract": 4,
    "terminology": 5,
    "precondition": 6,
    "lock": 7,
    "unexpected": 1,
}

_STOP_EXIT: Final[dict[StopReason, int]] = {
    StopReason.DONE: EXIT["ok"],
    StopReason.GATE: EXIT["gate"],
    StopReason.CAP: EXIT["cap"],
    StopReason.UNTIL: EXIT["until"],
    StopReason.DRY_RUN: EXIT["ok"],
    StopReason.FAILED: EXIT["unexpected"],
}

_WAIT_POLL_SECONDS: Final[int] = 15
_TERMINAL: Final[frozenset[str]] = frozenset({"awaiting_gate", "blocked", "complete", "failed"})


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ipfe",
        description="Run the Patent Drafting Playbook across seven OpenClaw agents.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging to stderr")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new-matter", help="create a dossier")
    new.add_argument("--notify", metavar="SESSION_KEY", help="push progress into this session")
    new.add_argument("--slug", required=True)
    new.add_argument("--title", required=True)
    new.add_argument("--jurisdictions", default="US,EP,GB")
    new.add_argument(
        "--from-example",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="seed 00-intake from an example matter (default: tests/fixtures/example-matter)",
    )

    check = sub.add_parser("check", help="preflight: prompts, agents, gateway")
    check.add_argument("--matter")
    check.add_argument(
        "--offline", action="store_true", help="skip every openclaw call (CI and tests)"
    )

    for name in ("run", "resume"):
        run = sub.add_parser(name, help="drive the matter to the next human decision")
        run.add_argument("--matter", required=True)
        run.add_argument("--until", choices=["gate1", "gate2", "review", "package"])
        run.add_argument(
            "--from",
            dest="from_phase",
            choices=["P1", "P2", "P3", "P4", "P5", "review", "package"],
        )
        run.add_argument("--dry-run", action="store_true")
        run.add_argument("--transport", choices=["auto", "gateway-call", "agent-cli", "fake"])
        run.add_argument("--fixtures", help="replay directory for --transport fake")
        run.add_argument("--max-inner-rounds", type=int)
        run.add_argument("--max-loopback-cycles", type=int)
        run.add_argument("--terminology", choices=["block", "warn"])
        run.add_argument("--allow-truncate", action="store_true")
        run.add_argument(
            "--notify",
            metavar="SESSION_KEY",
            help="push progress into this OpenClaw session (agent:<id>:<session>); remembered",
        )
        run.add_argument(
            "--notify-deliver", action="store_true", help="also deliver relays to the channel"
        )
        run.add_argument(
            "--notify-interval",
            type=int,
            metavar="SECONDS",
            help="seconds between pushed progress batches (default 60; urgent lines go at once)",
        )
        run.add_argument(
            "--unattended",
            action="store_true",
            help="auto-accept Gates 1, 2 and loop-back caps; stop only at Gate 3 (remembered)",
        )
        run.add_argument("--attended", action="store_true", help="clear a remembered --unattended")
        run.add_argument(
            "--search",
            action="store_true",
            help="opt in: send Prompt-06 search strings to Google Patents and chart the "
            "machine-retrieved candidates (remembered on the matter)",
        )
        run.add_argument("--no-search", action="store_true", help="clear a remembered --search")
        run.add_argument(
            "--notify-verbosity",
            choices=["steps", "milestones"],
            help="how chatty the pushed updates are (default: milestones when unattended)",
        )
        run.add_argument(
            "--background",
            action="store_true",
            help="launch detached and return at once; poll with `status --wait`",
        )

    intake = sub.add_parser("intake", help="put material into a matter's 00-intake")
    intake.add_argument("--matter", required=True)
    intake.add_argument(
        "--kind", required=True, choices=["disclosure", "prior-art", "priority", "data", "answers"]
    )
    intake.add_argument("--name", help="short name for prior-art/priority/data files")
    intake.add_argument("--file", help="copy this file (default: read stdin)")
    intake.add_argument("--stdin", action="store_true", help="read the content from stdin")
    intake.add_argument("--force", action="store_true", help="overwrite an existing file")

    status = sub.add_parser("status", help="where the matter is and what it needs")
    status.add_argument("--matter", required=True)
    status.add_argument("--json", dest="as_json", action="store_true")
    status.add_argument("--wait", type=int, metavar="SECONDS", help="poll until a decision is due")

    decide = sub.add_parser("record-decision", help="record a human gate decision")
    decide.add_argument("--matter", required=True)
    decide.add_argument("--gate", required=True)
    decide.add_argument("--decision", required=True)
    decide.add_argument("--by", required=True)
    decide.add_argument("--note")
    decide.add_argument("--answers-file")
    decide.add_argument("--notify", metavar="SESSION_KEY", help="push progress into this session")
    decide.add_argument("--confirmed-file")

    prompt = sub.add_parser("prompt", help="assemble one prompt and print it")
    prompt.add_argument("--matter", required=True)
    prompt.add_argument("--prompt", required=True)
    prompt.add_argument("--version", type=int, default=0)
    prompt.add_argument("--out", help="write to this file instead of stdout")

    show = sub.add_parser("show", help="inspect an artefact")
    show.add_argument("--matter", required=True)
    show.add_argument(
        "what",
        nargs="?",
        default="state",
        choices=["state", "log", "findings", "gates", "package", "runs", "terminology"],
    )
    show.add_argument("--tail", type=int, default=20)

    pkg = sub.add_parser("package", help="report (or rebuild) the packages")
    pkg.add_argument("--matter", required=True)
    pkg.add_argument("--rebuild", action="store_true")

    verify = sub.add_parser("verify-terminology", help="re-check the filing text against the lock")
    verify.add_argument("--matter", required=True)

    ev = sub.add_parser("evidence", help="build (or verify) the evidence pack for the package")
    ev.add_argument("--matter", required=True)
    ev.add_argument(
        "--verify",
        action="store_true",
        help="re-hash every file the existing pack lists instead of building one",
    )
    ev.add_argument(
        "--no-ledger", action="store_true", help="do not anchor the root hash in the repo ledger"
    )
    ev.add_argument("--json", dest="as_json", action="store_true")

    support = sub.add_parser(
        "verify-support", help="deterministic claim-support check (advisory, never blocking)"
    )
    support.add_argument("--matter", required=True)
    support.add_argument("--json", dest="as_json", action="store_true")

    ask = sub.add_parser("questions", help="rank the Phase 1 open questions for Gate 1")
    ask.add_argument("--matter", required=True)
    ask.add_argument("--top", type=int, default=10)
    ask.add_argument("--json", dest="as_json", action="store_true")
    ask.add_argument(
        "--template", action="store_true", help="write 00-intake/gate-1-answers.template.md"
    )

    repair = sub.add_parser("repair-state", help="rebuild state.json from the artefacts on disk")
    repair.add_argument("--matter", required=True)
    repair.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    handlers = {
        "new-matter": cmd_new_matter,
        "check": cmd_check,
        "run": cmd_run,
        "resume": cmd_run,
        "status": cmd_status,
        "record-decision": cmd_record_decision,
        "intake": cmd_intake,
        "prompt": cmd_prompt,
        "show": cmd_show,
        "package": cmd_package,
        "verify-terminology": cmd_verify_terminology,
        "evidence": cmd_evidence,
        "verify-support": cmd_verify_support,
        "questions": cmd_questions,
        "repair-state": cmd_repair_state,
    }
    try:
        return handlers[args.command](args)
    except gates_module.GateRefused as exc:
        return _fail(EXIT["usage"], f"refused: {exc}")
    except LockHeld as exc:
        return _fail(
            EXIT["lock"],
            f"{exc}. Another run owns this matter; wait for it or kill that process.",
        )
    except PromptTooLarge as exc:
        return _fail(EXIT["precondition"], f"prompt too large: {exc}")
    except ContractFailed as exc:
        return _fail(EXIT["contract"], f"output contract failed: {exc}")
    except TerminologyBlocked as exc:
        return _fail(EXIT["terminology"], f"terminology violation: {exc}")
    except GatewayError as exc:
        return _fail(EXIT["transport"], f"transport failure: {exc}")
    except (MissingInput, PlaybookError, StateError, gates_module.GateError) as exc:
        return _fail(EXIT["precondition"], f"precondition missing: {exc}")
    except DossierError as exc:
        return _fail(EXIT["precondition"], f"dossier: {exc}")
    except KeyboardInterrupt:
        return _fail(EXIT["unexpected"], "interrupted")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_new_matter(args: argparse.Namespace) -> int:
    settings = _settings(args)
    slug = args.slug.strip()
    if not slug or "/" in slug or slug != slug.lower():
        return _fail(EXIT["usage"], "--slug must be a lowercase path-safe name")
    root = settings.dossier_root(slug)
    dossier = Dossier(root, settings)
    if dossier.exists():
        return _fail(EXIT["usage"], f"matter {slug!r} already exists at {root}")

    example: Path | None = None
    if args.from_example is not None:
        example = Path(args.from_example) if args.from_example else _example_matter(settings)
    jurisdictions = [j.strip() for j in args.jurisdictions.split(",") if j.strip()]
    dossier.create(slug, args.title, jurisdictions, from_example=example)
    EventLog(dossier.log_path, slug).emit(
        "matter_created", title=args.title, jurisdictions=jurisdictions, example=example
    )
    if getattr(args, "notify", None):
        state = state_module.load(dossier.state_path)
        state.notify = state_module.NotifyTarget(session_key=args.notify)
        state_module.save(state, dossier.state_path)

    print(f"Created {root}")
    if example is None:
        print(f"\nPut the inventor disclosure at {root / '00-intake' / 'disclosure.md'}, then:")
    else:
        print(f"\nSeeded 00-intake from {example}. Then:")
    print(f"  uv run ipfe check --matter {slug}")
    print(f"  uv run ipfe run --matter {slug} --until gate1")
    print(f"  uv run ipfe status --matter {slug}")
    return EXIT["ok"]


def cmd_check(args: argparse.Namespace) -> int:
    settings = _settings(args)
    problems: list[str] = []
    notes: list[str] = []

    try:
        playbook = load_playbook(settings.prompts_path)
        notes.append(f"prompt library: {len(playbook.prompts)} prompts load and validate")
    except PlaybookError as exc:
        problems.extend(exc.problems)

    example = _example_matter(settings)
    if (example / "00-intake" / "disclosure.md").is_file():
        notes.append(f"example matter: {example.relative_to(settings.repo_root)}")
    else:
        problems.append(f"example matter missing: {example}")

    problems.extend(_check_agent_files(settings, notes))

    if args.matter:
        problems.extend(_check_matter(settings, args.matter, notes))

    if args.offline:
        notes.append("offline: gateway and agent registration not checked")
    else:
        problems.extend(_check_gateway(settings, notes))

    for note in notes:
        print(f"ok    {note}")
    for problem in problems:
        print(f"FAIL  {problem}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s).", file=sys.stderr)
        return EXIT["precondition"]
    print("\nAll checks passed.")
    return EXIT["ok"]


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dossier, state, events = _open(settings, args.matter)
    playbook = load_playbook(settings.prompts_path)

    if args.dry_run:
        runner = Runner(
            dossier, state, playbook, _NullTransport(), settings, events, args.allow_truncate
        )
        runner.run(until=args.until, from_phase=args.from_phase, dry_run=True)
        report = runner.dry_run_report
        assert report is not None
        print(f"dry run: next stage is {report.stage}; no agent was called.\n")
        print(report.table())
        print(f"\nPrompts written under {dossier.runs_dir}")
        return EXIT["ok"]

    if state.status in {"complete"}:
        print(f"matter {args.matter} is complete; nothing to run.")
        return EXIT["ok"]

    notifier = _attach_notifier(args, dossier, state, events, settings)
    if args.background:
        return _spawn_background(args, dossier, settings)

    transport = _transport(args, settings)
    try:
        with Lock(dossier.root):
            # Re-read under the lock: another process may have decided a gate
            # between our load and our turn.
            state = state_module.load(dossier.state_path)
            runner = Runner(
                dossier, state, playbook, transport, settings, events, args.allow_truncate
            )
            reason = runner.run(until=args.until, from_phase=args.from_phase)
    finally:
        # Whatever happened, the last ticker lines reach the human.
        if notifier is not None:
            notifier.close()

    _print_stop(reason, dossier, state, settings)
    return _STOP_EXIT[reason]


def _attach_notifier(
    args: argparse.Namespace,
    dossier: Dossier,
    state: MatterState,
    events: EventLog,
    settings: Settings,
) -> SessionNotifier | None:
    """Remember a `--notify` target on the matter and subscribe the relay to the log.

    The target is persisted so a background child, a later `run`, and
    `record-decision` all push to the same session without being told again.
    """
    changed = False
    if getattr(args, "unattended", False):
        state.unattended = True
        changed = True
    if getattr(args, "attended", False):
        state.unattended = False
        changed = True
    if getattr(args, "search", False):
        state.search = True
        changed = True
    if getattr(args, "no_search", False):
        state.search = False
        changed = True
    key = getattr(args, "notify", None)
    if key:
        deliver = bool(getattr(args, "notify_deliver", False))
        state.notify = state_module.NotifyTarget(session_key=key, deliver=deliver)
        changed = True
    verbosity = getattr(args, "notify_verbosity", None)
    if state.notify is not None and (verbosity or getattr(args, "unattended", False)):
        state.notify.verbosity = verbosity or "milestones"
        changed = True
    if changed:
        state_module.save(state, dossier.state_path)
    target = state.notify
    if target is None:
        return None
    try:
        playbook = load_playbook(settings.prompts_path)
        titles = {pid: spec.title for pid, spec in playbook.prompts.items()}
    except Exception:  # the relay must never depend on the prompt library loading
        titles = {}
    notifier = SessionNotifier(
        target.session_key,
        settings.resolve_openclaw_bin(),
        deliver=target.deliver,
        log_path=dossier.root / "notify.log",
        progress_path=dossier.progress_path,
        prompt_titles=titles,
        verbosity=target.verbosity,
        interval_s=int(getattr(args, "notify_interval", None) or settings.notify_interval_s),
        matter=dossier.slug,
    )
    events.subscribe(notifier.on_event)
    return notifier


_BACKGROUND_PASSTHROUGH: Final[tuple[str, ...]] = (
    "until",
    "from_phase",
    "transport",
    "fixtures",
    "max_inner_rounds",
    "max_loopback_cycles",
    "terminology",
)


def _spawn_background(args: argparse.Namespace, dossier: Dossier, settings: Settings) -> int:
    """Start the run as a detached child and return at once.

    The front-door agent drives the ecosystem through an exec tool with a
    turn-length timeout; a run is hours. Detaching keeps the run alive across
    that turn, and `status --wait` is how anyone watches it.
    """
    try:
        with Lock(dossier.root):
            pass
    except LockHeld as exc:
        return _fail(EXIT["lock"], str(exc))

    argv = [sys.executable, "-m", "ecosystem", "run", "--matter", dossier.slug]
    for name in _BACKGROUND_PASSTHROUGH:
        value = getattr(args, name, None)
        if value is not None:
            argv += [f"--{name.replace('_', '-').replace('from-phase', 'from')}", str(value)]
    if args.allow_truncate:
        argv.append("--allow-truncate")

    log_path = dossier.root / "run.log"
    with log_path.open("ab") as log:
        log.write(f"\n== ipfe run started {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n".encode())
        child = subprocess.Popen(  # noqa: S603 - argv is built from our own flags
            argv,
            cwd=settings.repo_root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (dossier.root / "run.pid").write_text(str(child.pid))
    print(f"run started in background for matter {dossier.slug} (pid {child.pid})")
    print(f"log: {log_path}")
    print(f"next: uv run ipfe status --matter {dossier.slug} --wait 240")
    return EXIT["ok"]


_INTAKE_SINGLE: Final[dict[str, str]] = {
    "disclosure": "disclosure.md",
    "answers": "gate-1-answers.md",
}
_INTAKE_DIR: Final[dict[str, str]] = {
    "prior-art": "prior-art",
    "priority": "priority",
    "data": "data",
}


def _slugify(name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in name.strip()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:64] or "item"


def cmd_intake(args: argparse.Namespace) -> int:
    """Write intake material for a matter. The only sanctioned writer into 00-intake."""
    settings = _settings(args)
    dossier, _state, events = _open(settings, args.matter)
    if args.file and args.stdin:
        return _fail(EXIT["usage"], "pass either --file or --stdin, not both")
    if args.file:
        source = Path(args.file).expanduser()
        if not source.is_file():
            return _fail(EXIT["usage"], f"no such file: {source}")
        content = source.read_text(encoding="utf-8")
    else:
        content = sys.stdin.read()
    if not content.strip():
        return _fail(EXIT["usage"], "intake content is empty")

    if args.kind in _INTAKE_SINGLE:
        target = dossier.intake_dir / _INTAKE_SINGLE[args.kind]
    else:
        name = args.name or (Path(args.file).stem if args.file else None)
        if not name:
            return _fail(EXIT["usage"], f"--kind {args.kind} needs --name <short-name>")
        target = dossier.intake_dir / _INTAKE_DIR[args.kind] / f"{_slugify(name)}.md"
    if target.exists() and not args.force:
        return _fail(EXIT["usage"], f"{target} exists; pass --force to overwrite")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    events.emit("intake_written", kind=args.kind, path=str(target.relative_to(dossier.root)))
    print(f"wrote {target} ({len(content):,} chars)")
    return EXIT["ok"]


def cmd_status(args: argparse.Namespace) -> int:
    settings = _settings(args)
    deadline = time.monotonic() + (args.wait or 0)
    while True:
        dossier, state, _ = _open(settings, args.matter)
        report = _status_report(dossier, state, settings)
        if not args.wait or state.status in _TERMINAL or time.monotonic() >= deadline:
            break
        time.sleep(min(_WAIT_POLL_SECONDS, max(1, int(deadline - time.monotonic()))))

    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(_render_status(report))
    return EXIT["ok"]


def cmd_record_decision(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dossier, state, events = _open(settings, args.matter)
    with Lock(dossier.root):
        state = state_module.load(dossier.state_path)
        events = EventLog(dossier.log_path, dossier.slug)
        _attach_notifier(args, dossier, state, events, settings)
        gates = gates_module.Gates(dossier, state, settings, events)
        result = gates.decide(
            args.gate,
            args.decision,
            args.by,
            note=args.note,
            answers_file=args.answers_file,
            confirmed_file=args.confirmed_file,
        )
        state_module.save(state, dossier.state_path)

    if result.status == "already_decided":
        print(f"gate {args.gate} was already decided {args.decision!r}; nothing changed.")
        return EXIT["ok"]
    print(f"gate {args.gate}: {args.decision} (by {args.by})")
    for effect in result.effects:
        print(f"  - {effect}")
    if state.status == "failed":
        print("\nThe matter is marked failed. Nothing further will run.")
        return EXIT["ok"]
    print(f"\nNext:  uv run ipfe run --matter {dossier.slug}")
    return EXIT["ok"]


def cmd_prompt(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dossier, state, _ = _open(settings, args.matter)
    playbook = load_playbook(settings.prompts_path)
    try:
        spec = playbook.get(args.prompt)
    except KeyError as exc:
        return _fail(EXIT["usage"], str(exc))

    version = args.version or (state.phase(spec.phase).current_version or 1)
    ctx = InputContext(phase=spec.phase, prompt_id=spec.id)
    assembled = assemble(
        dossier, state, spec, ctx, settings, allow_truncate=True, tolerate_missing=True
    )
    if args.out:
        Path(args.out).write_text(assembled.text, encoding="utf-8")
        print(f"{args.out} ({assembled.chars} chars, {assembled.transport_hint})")
    else:
        print(assembled.text)
    if assembled.missing_required:
        print(
            f"\n[missing required inputs: {', '.join(assembled.missing_required)}]",
            file=sys.stderr,
        )
    _ = version
    return EXIT["ok"]


def cmd_show(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dossier, state, events = _open(settings, args.matter)
    what = args.what
    if what == "state":
        print(json.dumps(state_module.as_json(state), indent=2))
    elif what == "log":
        for record in events.tail(args.tail):
            print(json.dumps(record))
    elif what == "findings":
        review = dossier.latest("REVIEW") or _newest(dossier, "REVIEW")
        if review is None:
            return _fail(EXIT["precondition"], "no review version yet")
        findings = loopback.load_findings(review.path)
        print(json.dumps([f.model_dump(mode="json", by_alias=True) for f in findings], indent=2))
    elif what == "gates":
        for path in sorted(dossier.gates_dir.glob("*.json")):
            print(f"--- {path.name}")
            print(path.read_text(encoding="utf-8"))
    elif what == "package":
        for version in (dossier.latest("PACKAGE"), dossier.latest("REVIEW")):
            if version is None:
                continue
            print(f"--- {version.path}")
            for item in sorted(version.path.rglob("*")):
                if item.is_file():
                    print(f"  {item.relative_to(version.path)}  ({item.stat().st_size} bytes)")
    elif what == "runs":
        for run in state.agent_runs[-args.tail :]:
            print(
                f"{run.call_id:<48} {run.agent_id:<16} {run.status:<8} "
                f"{run.duration_ms or 0:>7}ms  {run.prompt_chars or 0:>8} chars"
            )
    elif what == "terminology":
        path = dossier.locked_terminology_path
        print(path.read_text(encoding="utf-8") if path.is_file() else "not locked yet")
    return EXIT["ok"]


def cmd_package(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dossier, state, events = _open(settings, args.matter)
    if args.rebuild:
        with Lock(dossier.root):
            state = state_module.load(dossier.state_path)
            target = package.build_final_package(dossier, state)
            state_module.save(state, dossier.state_path)
        events.emit("package_built", phase="PACKAGE", path=target, kind="final")
        print(f"rebuilt {target}")
        return EXIT["ok"]

    version = dossier.latest("PACKAGE")
    if version is None:
        return _fail(EXIT["precondition"], "no package has been built yet")
    manifest_path = version.path / "MANIFEST.json"
    print(version.path)
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for member in manifest.get("members", []):
            print(f"  {member['name']:<24} {member['sha256'][:12]}  from {member['source']}")
        print(f"\n{manifest.get('requires', '')}")
    return EXIT["ok"]


def cmd_verify_terminology(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dossier, state, _ = _open(settings, args.matter)
    locked = dossier.terminology_dir / "LOCKED.json"
    if not locked.is_file():
        return _fail(EXIT["precondition"], "terminology is not locked (Gate 1 not decided)")
    table = LockedTable.from_json(locked.read_text(encoding="utf-8"))

    violations = 0
    for phase, filename in (("P4", "description.md"), ("P5", "claims.md")):
        text = _latest_text(dossier, phase, filename)
        if text is None:
            continue
        report = check_output(text, None, table, "drafting", filename)
        print(
            f"{filename}: {len(report.violations)} violation(s), {len(report.warnings)} warning(s)"
        )
        for violation in report.violations:
            print(f"  VIOLATION {violation.rule}: {violation.term} (locked row {violation.row})")
            print(f"            ...{violation.context}...")
        violations += len(report.violations)
    _ = state
    if violations:
        return EXIT["terminology"]
    print("\nNo terminology violations in the current filing text.")
    return EXIT["ok"]


def cmd_evidence(args: argparse.Namespace) -> int:
    """Build or verify the evidence pack for the latest final package.

    `--verify` is the command a reviewer runs: exit 4 means a file the pack
    vouches for has changed or gone, which is a fact about the dossier, not a
    transport or contract failure.
    """
    settings = _settings(args)
    dossier, state, events = _open(settings, args.matter)
    version = dossier.latest("PACKAGE") or _newest(dossier, "PACKAGE")
    if version is None:
        return _fail(
            EXIT["precondition"],
            f"no package has been built for {args.matter}; there is nothing to evidence",
        )

    if args.verify:
        return _verify_evidence(dossier, version.v, args.as_json)

    with Lock(dossier.root):
        state = state_module.load(dossier.state_path)
        directory = evidence_module.build_evidence(dossier, state, version.path)
        ledger_path: Path | None = None
        if not args.no_ledger:
            try:
                package.anchor_root_hash(dossier, directory)
                ledger_path = ledger_module.ledger_path(settings.repo_root, dossier.slug)
                ledger_path = ledger_path if ledger_path.is_file() else None
            except Exception as exc:  # anchoring never fails the command
                log.warning("ledger anchoring failed: %s", exc)

    manifest = json.loads((directory / evidence_module.MANIFEST_NAME).read_text(encoding="utf-8"))
    events.emit(
        "package_built",
        phase="PACKAGE",
        path=directory,
        kind="evidence",
        root_hash=manifest.get("dossier_root_hash"),
    )
    report = {
        "matter": dossier.slug,
        "package_version": manifest.get("package_version"),
        "evidence_dir": str(directory),
        "root_hash": manifest.get("dossier_root_hash"),
        "files": manifest.get("file_count"),
        "ledger": str(ledger_path) if ledger_path else None,
    }
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"evidence pack  {report['evidence_dir']}")
        print(f"root hash      {report['root_hash']}")
        print(f"files          {report['files']}")
        print(f"ledger         {report['ledger'] or 'not anchored'}")
        print(f"\nverify with:   uv run ipfe evidence --matter {dossier.slug} --verify")
    return EXIT["ok"]


def _verify_evidence(dossier: Dossier, package_version: int, as_json: bool) -> int:
    directory = evidence_module.latest_evidence_dir(
        dossier, package_version
    ) or evidence_module.latest_evidence_dir(dossier)
    if directory is None:
        return _fail(
            EXIT["precondition"],
            f"no evidence pack for {dossier.slug}; build one with "
            f"`uv run ipfe evidence --matter {dossier.slug}`",
        )
    report = evidence_module.verify_evidence(dossier, directory)
    if as_json:
        print(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        print(f"evidence pack  {directory}")
        print(f"root hash      {report.recorded_root_hash}")
        print(f"result         {report.summary()}")
        for path in report.missing:
            print(f"  MISSING   {path}")
        for mismatch in report.mismatched:
            print(f"  CHANGED   {mismatch.path}")
            print(f"            recorded {mismatch.recorded[:16]} actual {mismatch.actual[:16]}")
        if not report.ok:
            print(f"\nrecomputed root hash: {report.actual_root_hash}")
    return EXIT["ok"] if report.ok else EXIT["contract"]


def cmd_verify_support(args: argparse.Namespace) -> int:
    """Advisory claim-support check. Always exits 0 unless there is nothing to check."""
    settings = _settings(args)
    dossier, _state, _ = _open(settings, args.matter)
    description = _filing_text(dossier, "P4", "description.md")
    claims = _filing_text(dossier, "P5", "claims.md")
    missing = [
        name
        for name, text in (("description.md", description), ("claims.md", claims))
        if text is None
    ]
    if missing:
        return _fail(
            EXIT["precondition"],
            f"no {', '.join(missing)} for {args.matter}; the inner loop has not produced "
            "the filing text yet",
        )
    report = support_module.check_support(description or "", claims or "")
    if args.as_json:
        print(report.to_json())
    else:
        print(report.to_markdown())
        print(
            "\nAdvisory only: this is a word-level heuristic that complements the "
            "independent reviewer's Prompt 28 support matrix and never replaces it."
        )
    return EXIT["ok"]


def _filing_text(dossier: Dossier, phase: str, filename: str) -> str | None:
    """Newest filing text: the phase version, else the review package, else the package."""
    text = _latest_text(dossier, phase, filename)
    if text is not None:
        return text
    for version in (dossier.latest("REVIEW"), _newest(dossier, "REVIEW")):
        if version is not None:
            candidate = version.path / "package" / filename
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
    package_version = dossier.latest("PACKAGE") or _newest(dossier, "PACKAGE")
    if package_version is not None:
        candidate = package_version.path / filename
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None


def cmd_questions(args: argparse.Namespace) -> int:
    """Rank Phase 1's open questions so the human answers the scope-critical ones first."""
    settings = _settings(args)
    dossier, _state, _ = _open(settings, args.matter)
    if not dossier.versions("P1"):
        return _fail(
            EXIT["precondition"],
            f"Phase 1 has not run for {args.matter}; there are no open questions to rank",
        )
    ranked = questions_module.top_questions(dossier, args.top)

    template: Path | None = None
    if args.template:
        template = questions_module.write_answers_template(dossier, ranked)

    if args.as_json:
        payload = {
            "matter": dossier.slug,
            "questions": [question.model_dump(mode="json") for question in ranked],
            "template": str(template) if template else None,
        }
        print(json.dumps(payload, indent=2))
        return EXIT["ok"]

    if not ranked:
        print(f"Phase 1 recorded no open questions for {dossier.slug}.")
    for question in ranked:
        blocks = ", ".join(question.blocks) or "-"
        print(f"{question.rank:>2}. {question.id:<8} score {question.score:<3} blocks {blocks}")
        print(f"    {question.text}")
        print(f"    why: {question.why}")
    if template is not None:
        print(f"\nanswers template: {template}")
        print(
            f"then: uv run ipfe record-decision --matter {dossier.slug} --gate 1 "
            f"--decision accept --by <you> --answers-file {template}"
        )
    return EXIT["ok"]


def cmd_repair_state(args: argparse.Namespace) -> int:
    settings = _settings(args)
    root = settings.dossier_root(args.matter)
    if not (root / "matter.json").is_file():
        return _fail(EXIT["precondition"], f"no matter at {root}")
    repaired = state_module.repair_from_disk(root)
    if args.dry_run:
        print(json.dumps(state_module.as_json(repaired), indent=2))
        return EXIT["ok"]
    with Lock(root):
        state_module.save(repaired, root / "state.json")
    print(f"rebuilt {root / 'state.json'} from disk")
    for phase, phase_state in repaired.phases.items():
        print(
            f"  {phase:<8} {phase_state.status:<9} v{phase_state.current_version or 0} "
            f"({len(phase_state.versions)} version(s))"
        )
    return EXIT["ok"]


# ---------------------------------------------------------------------------
# status rendering
# ---------------------------------------------------------------------------


def _status_report(dossier: Dossier, state: MatterState, settings: Settings) -> dict[str, Any]:
    report: dict[str, Any] = {
        "matter": state.matter.slug,
        "title": state.matter.title,
        "status": state.status,
        "cursor": state.cursor.model_dump(),
        "phases": {
            phase: {
                "agent": ps.agent,
                "status": ps.status,
                "current_version": ps.current_version,
                "versions": len(ps.versions),
            }
            for phase, ps in state.phases.items()
        },
        "gates": {
            key: {
                "status": gs.status,
                "decision": gs.decision,
                "decided_by": gs.decided_by,
                "decided_at": gs.decided_at,
            }
            for key, gs in state.gates.items()
        },
        "inner_loop": state.inner_loop.model_dump(),
        "loopback": {
            "cycles": state.loopback.cycles,
            "cap_per_category": settings.loopback_cap,
            "formal_fix_passes": state.loopback.formal_fix_passes,
            "forced_decisions": [d.model_dump() for d in state.loopback.forced_decisions],
        },
        "review": [v.model_dump() for v in state.review.versions],
        "terminology": state.terminology.model_dump(),
        "counters": state.counters,
        "last_error": state.last_error.model_dump() if state.last_error else None,
    }
    pending = [key for key, gs in state.gates.items() if gs.status == "pending"]
    if pending:
        report["awaiting_decision"] = _awaiting(dossier, state, pending[0])
    report["progress"] = _progress_tail(dossier, 12)
    return report


def _awaiting(dossier: Dossier, state: MatterState, gate: str) -> dict[str, Any]:
    """The block a human acts on. `how_to_answer` is literal and ready to run."""
    stem = gates_module.gate_stem(gate)
    brief = dossier.briefs_dir / f"{stem}.md"
    pending_path = dossier.gates_dir / f"{stem}.pending.json"
    payload: dict[str, Any] = {}
    if pending_path.is_file():
        try:
            payload = json.loads(pending_path.read_text(encoding="utf-8")).get("payload", {})
        except json.JSONDecodeError:
            payload = {}

    default = gates_module.default_decision(gate)
    slug = state.matter.slug
    command = (
        f"uv run ipfe record-decision --matter {slug} --gate {gate} "
        f"--decision {default} --by operator"
    )
    return {
        "gate": gate,
        "brief": str(brief),
        "show_the_user": payload.get("summary")
        or (brief.read_text(encoding="utf-8")[:800] if brief.is_file() else ""),
        "options": gates_module.options_for(gate),
        "how_to_answer": command,
        "then": f"uv run ipfe run --matter {slug}",
        "blocks": state.gate(gate).blocks,
        "files": payload.get("files", []),
    }


def _progress_tail(dossier: Dossier, n: int) -> list[str]:
    """The last ticker lines: what the run is doing, without reading the log."""
    path = dossier.progress_path
    if not path.is_file():
        return []
    try:
        return [
            line.rstrip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ][-n:]
    except OSError:
        return []


def _render_status(report: dict[str, Any]) -> str:
    lines = [
        f"matter      {report['matter']}  ({report['title']})",
        f"status      {report['status']}",
        f"cursor      {report['cursor'].get('phase') or '-'} / "
        f"{report['cursor'].get('step') or '-'}"
        f"  round {report['cursor'].get('inner_round', 0)}",
        "",
        "phase    agent             status     version",
    ]
    for phase in PHASES:
        info = report["phases"].get(phase, {})
        lines.append(
            f"{phase:<8} {info.get('agent', ''):<17} {info.get('status', ''):<10} "
            f"v{info.get('current_version') or 0}"
        )
    lines += ["", "gate                      status         decision   by"]
    for key, gate in report["gates"].items():
        lines.append(
            f"{key:<25} {gate['status']:<14} {str(gate['decision'] or '-'):<10} "
            f"{gate['decided_by'] or '-'}"
        )
    inner = report["inner_loop"]
    lines += [
        "",
        f"inner loop  round {inner['round']}/{inner['cap']}  converged={inner['converged']}",
        f"loop-back   {report['loopback']['cycles']}  "
        f"(cap {report['loopback']['cap_per_category']} per category, "
        f"formal passes {report['loopback']['formal_fix_passes']})",
        f"terminology {report['terminology']['terms']} terms locked, "
        f"{report['terminology']['violations_blocked']} violations blocked",
        f"calls       {report['counters'].get('calls', 0)} "
        f"({report['counters'].get('contract_corrections', 0)} contract corrections)",
    ]
    if report.get("progress"):
        lines += ["", "recent      (dossiers/<slug>/progress.md has the full ticker)"]
        lines += [f"  {line}" for line in report["progress"]]
    if report.get("last_error"):
        error = report["last_error"]
        lines += ["", f"last error  {error['kind']}: {error['message'][:160]}"]

    awaiting = report.get("awaiting_decision")
    if awaiting:
        lines += [
            "",
            "=" * 72,
            f"awaiting_decision  gate {awaiting['gate']}",
            "=" * 72,
            f"brief:          {awaiting['brief']}",
            f"options:        {', '.join(awaiting['options'])}",
            f"blocks:         {', '.join(awaiting['blocks']) or '-'}",
            "",
            "show_the_user:",
            _indent(awaiting["show_the_user"]),
            "",
            "how_to_answer:",
            f"  {awaiting['how_to_answer']}",
            "",
            "then:",
            f"  {awaiting['then']}",
        ]
    return "\n".join(lines)


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in (text or "").splitlines()) or f"{prefix}-"


def _print_stop(
    reason: StopReason, dossier: Dossier, state: MatterState, settings: Settings
) -> None:
    if reason is StopReason.GATE or reason is StopReason.CAP:
        report = _status_report(dossier, state, settings)
        awaiting = report.get("awaiting_decision")
        if awaiting:
            print(_render_status({**report, "awaiting_decision": awaiting}))
            return
    if reason is StopReason.UNTIL:
        print(f"stopped at --until; matter {state.matter.slug} is {state.status}.")
        return
    if reason is StopReason.FAILED:
        print(f"matter {state.matter.slug} is failed.", file=sys.stderr)
        return
    print(f"matter {state.matter.slug}: {state.status}.")


# ---------------------------------------------------------------------------
# preflight helpers
# ---------------------------------------------------------------------------


def _check_agent_files(settings: Settings, notes: list[str]) -> list[str]:
    problems: list[str] = []
    agents_dir = settings.repo_root / "agents"
    shared = agents_dir / "_shared" / "USER.md"
    if not shared.is_file():
        problems.append(f"missing {shared}")
    elif len(shared.read_text(encoding="utf-8")) > 4000:
        problems.append("agents/_shared/USER.md is over the 4 000 char USER.md cap")
    for agent in AGENT_IDS:
        for name in ("SOUL.md", "AGENTS.md", "IDENTITY.md"):
            path = agents_dir / agent / name
            if not path.is_file():
                problems.append(f"missing {path.relative_to(settings.repo_root)}")
                continue
            size = len(path.read_text(encoding="utf-8"))
            if size > 20_000:
                problems.append(
                    f"{path.relative_to(settings.repo_root)} is {size} chars, over the "
                    "20 000 char bootstrap cap"
                )
    if not problems:
        notes.append(f"agent workspace files: {len(AGENT_IDS)} agents within bootstrap caps")
    return problems


def _check_matter(settings: Settings, slug: str, notes: list[str]) -> list[str]:
    root = settings.dossier_root(slug)
    dossier = Dossier(root, settings)
    if not dossier.exists():
        return [f"no matter at {root}"]
    disclosure = dossier.intake_dir / "disclosure.md"
    if not disclosure.is_file():
        return [f"missing {disclosure}: Phase 1 has nothing to read"]
    notes.append(f"matter {slug}: intake present ({disclosure.stat().st_size} bytes)")
    try:
        state_module.load(dossier.state_path)
    except StateError as exc:
        return [str(exc)]
    notes.append(f"matter {slug}: state.json loads")
    return []


def _check_gateway(settings: Settings, notes: list[str]) -> list[str]:
    try:
        binary = settings.resolve_openclaw_bin()
    except FileNotFoundError as exc:
        return [str(exc)]
    problems: list[str] = []

    health = _run_openclaw([binary, "gateway", "health"])
    if health is None:
        problems.append("openclaw gateway health failed (is the gateway running?)")
    else:
        notes.append("openclaw gateway health ok")

    listing = _run_openclaw([binary, "agents", "list", "--json"])
    if listing is None:
        problems.append("openclaw agents list --json failed")
        return problems
    registered = set(_agent_ids(listing))
    missing = [agent for agent in AGENT_IDS if agent not in registered]
    if missing:
        problems.append(
            f"not registered with OpenClaw: {', '.join(missing)} (run scripts/install.sh)"
        )
    else:
        notes.append(f"openclaw agents list: all {len(AGENT_IDS)} ip-* agents registered")
    return problems


def _run_openclaw(argv: list[str]) -> str | None:
    """Read-only openclaw call. Never used for anything that changes state."""
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("%s failed: %s", " ".join(argv[1:]), exc)
        return None
    if completed.returncode != 0:
        log.warning("%s exited %s: %s", " ".join(argv[1:]), completed.returncode, completed.stderr)
        return None
    return completed.stdout


def _agent_ids(listing: str) -> list[str]:
    try:
        data = json.loads(listing)
    except json.JSONDecodeError:
        return []
    entries = data.get("agents", data) if isinstance(data, dict) else data
    if isinstance(entries, dict):
        return list(entries)
    out: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            value = entry.get("id") or entry.get("agentId") or entry.get("name")
            if value:
                out.append(str(value))
    return out


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------


class _NullTransport:
    """Refuses to call. `--dry-run` must not be able to reach a gateway by accident."""

    name = "null"

    def call(self, req: Any) -> Any:  # pragma: no cover - defensive
        raise GatewayError("dry run: no transport is configured")


def _settings(args: argparse.Namespace) -> Settings:
    overrides: dict[str, Any] = {}
    if getattr(args, "max_inner_rounds", None):
        overrides["inner_loop_cap"] = args.max_inner_rounds
    if getattr(args, "max_loopback_cycles", None):
        overrides["loopback_cap"] = args.max_loopback_cycles
    if getattr(args, "terminology", None):
        overrides["terminology_policy"] = args.terminology
    return Settings.from_env(None, **overrides)


def _transport(args: argparse.Namespace, settings: Settings) -> Transport:
    """Pick a transport. `fake:<dir>` is how the tests drive the whole CLI."""
    choice = getattr(args, "transport", None)
    fixtures = getattr(args, "fixtures", None)
    env = os.environ.get("IPFE_TRANSPORT", "")
    if choice is None and env:
        if env.startswith("fake"):
            choice = "fake"
            fixtures = fixtures or (env.split(":", 1)[1] if ":" in env else None)
        else:
            choice = env
    if choice == "fake":
        if not fixtures:
            raise gates_module.GateRefused(
                "--transport fake needs --fixtures DIR (or IPFE_TRANSPORT=fake:<dir>)"
            )
        from tests.fakes import FakeTransport  # test-only dependency, imported lazily

        return FakeTransport(mode="replay", fixtures_dir=Path(fixtures))
    force = choice if choice in {"gateway-call", "agent-cli"} else None
    return AutoTransport(settings, force=force)


def _open(settings: Settings, slug: str) -> tuple[Dossier, MatterState, EventLog]:
    root = settings.dossier_root(slug)
    dossier = Dossier(root, settings)
    if not dossier.exists():
        raise DossierError(f"no matter at {root}; run `ipfe new-matter --slug {slug} ...` first")
    state = state_module.load(dossier.state_path)
    return dossier, state, EventLog(dossier.log_path, slug)


def _example_matter(settings: Settings) -> Path:
    return settings.repo_root / "tests" / "fixtures" / "example-matter"


def _newest(dossier: Dossier, phase: str) -> Any:
    versions = dossier.versions(phase)
    return versions[-1] if versions else None


def _latest_text(dossier: Dossier, phase: str, filename: str) -> str | None:
    for version_dir in reversed(dossier.versions(phase)):
        candidate = version_dir.path / filename
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None


def _fail(code: int, message: str) -> int:
    print(message, file=sys.stderr)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
