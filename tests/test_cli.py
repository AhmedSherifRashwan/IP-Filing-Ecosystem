"""The command line: exit codes, the literal `how_to_answer`, idempotent decisions.

The exit codes are the contract a wrapper script depends on, so they are
asserted as numbers rather than through the enum: a refactor that renames a
StopReason must not silently change what `echo $?` says.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ecosystem.cli import EXIT, main
from ecosystem.playbook import load_playbook
from tests.conftest import REPO_ROOT
from tests.replay_builder import (
    Scenario,
    claims_correction,
    fixture_key,
    gate14_answers,
    materialise,
    novelty_finding,
)

EXAMPLE = REPO_ROOT / "tests" / "fixtures" / "example-matter"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at a temp dossiers dir; everything else is the real repo."""
    dossiers = tmp_path / "dossiers"
    monkeypatch.setenv("IPFE_DOSSIERS_DIR", str(dossiers))
    monkeypatch.delenv("IPFE_TRANSPORT", raising=False)
    monkeypatch.chdir(REPO_ROOT)
    return dossiers


@pytest.fixture
def replays(tmp_path: Path) -> Path:
    playbook = load_playbook(REPO_ROOT / "prompts")
    directory = tmp_path / "replays"
    scenario = (
        Scenario(slug="demo")
        .at(
            fixture_key("demo", "P5", "27", 1),
            envelope_extra={
                "disclosure_requests": [{"limitation": "threshold", "needed_disclosure": "range"}]
            },
        )
        .at(fixture_key("demo", "REVIEW", "32", 1), findings=[novelty_finding()])
        .at(fixture_key("demo", "REVIEW", "37", 1), corrections=[claims_correction()])
        .at(fixture_key("demo", "REVIEW", "gate14", 2), gate14=gate14_answers())
    )
    materialise(playbook, directory, scenario)
    return directory


def _new_matter(slug: str = "demo") -> int:
    return main(
        [
            "new-matter",
            "--slug",
            slug,
            "--title",
            "Self-purging bicycle tube valve",
            "--jurisdictions",
            "US,EP",
            "--from-example",
        ]
    )


def _run(replays: Path, *extra: str, slug: str = "demo") -> int:
    return main(
        ["run", "--matter", slug, "--transport", "fake", "--fixtures", str(replays), *extra]
    )


# ---------------------------------------------------------------------------
# new-matter and check
# ---------------------------------------------------------------------------


def test_new_matter_creates_the_tree_and_prints_the_next_commands(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _new_matter() == EXIT["ok"]

    out = capsys.readouterr().out
    assert "uv run ipfe run --matter demo --until gate1" in out
    root = env / "demo"
    assert (root / "matter.json").is_file()
    assert (root / "state.json").is_file()
    assert (root / "00-intake" / "disclosure.md").is_file()
    assert (root / "06-review").is_dir()


def test_new_matter_refuses_to_overwrite(env: Path) -> None:
    _new_matter()
    assert _new_matter() == EXIT["usage"]


def test_new_matter_refuses_an_unsafe_slug(env: Path) -> None:
    assert main(["new-matter", "--slug", "A/B", "--title", "x"]) == EXIT["usage"]


def test_check_offline_passes_without_a_gateway(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", "--offline"]) == EXIT["ok"]

    out = capsys.readouterr().out
    assert "prompt library" in out
    assert "agent workspace files" in out
    assert "offline" in out


def test_check_reports_a_matter_without_intake(env: Path) -> None:
    _new_matter("bare")
    (env / "bare" / "00-intake" / "disclosure.md").unlink()
    assert main(["check", "--offline", "--matter", "bare"]) == EXIT["precondition"]


def test_check_on_an_unknown_matter_is_a_precondition_failure(env: Path) -> None:
    assert main(["check", "--offline", "--matter", "nope"]) == EXIT["precondition"]


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_dry_run_prints_the_table_and_calls_nothing(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _new_matter()
    capsys.readouterr()

    assert main(["run", "--matter", "demo", "--until", "gate1", "--dry-run"]) == EXIT["ok"]
    out = capsys.readouterr().out

    header = out.splitlines()[2]
    assert header.split("|")[0].strip() == "prompt"
    assert [part.strip() for part in header.split("|")] == [
        "prompt",
        "chars",
        "transport",
        "inputs",
    ]
    for prompt_id in ("01", "02", "03", "04", "05"):
        assert f"\n{prompt_id} " in out or out.startswith(prompt_id)
    assert "gateway-call" in out
    assert "intake:disclosure" in out
    # Prompts are written; no reply ever comes back.
    runs = env / "demo" / "runs"
    assert (runs / "demo-p1-01-v1-a0" / "prompt.md").is_file()
    assert not (runs / "demo-p1-01-v1-a0" / "reply.md").exists()


def test_run_stops_at_gate_1_with_exit_10(
    env: Path, replays: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _new_matter()
    capsys.readouterr()

    assert _run(replays) == EXIT["gate"]
    out = capsys.readouterr().out
    assert "awaiting_decision  gate 1" in out
    assert "how_to_answer:" in out
    assert (
        "uv run ipfe record-decision --matter demo --gate 1 --decision accept --by operator" in out
    )


def test_unknown_matter_is_a_precondition_failure(env: Path, replays: Path) -> None:
    assert _run(replays, slug="ghost") == EXIT["precondition"]


def test_until_stops_with_exit_12(env: Path, replays: Path) -> None:
    _new_matter()
    _run(replays)
    main(
        [
            "record-decision",
            "--matter",
            "demo",
            "--gate",
            "1",
            "--decision",
            "accept",
            "--by",
            "ahmed",
        ]
    )
    assert _run(replays) == EXIT["gate"]  # Gate 2
    main(
        [
            "record-decision",
            "--matter",
            "demo",
            "--gate",
            "2",
            "--decision",
            "accept",
            "--by",
            "ahmed",
        ]
    )

    # The review round loops back through Phase 3 and asks for a Gate 2 delta.
    assert _run(replays, "--until", "review") == EXIT["gate"]
    main(
        [
            "record-decision",
            "--matter",
            "demo",
            "--gate",
            "2",
            "--decision",
            "accept",
            "--by",
            "ahmed",
        ]
    )
    assert _run(replays, "--until", "review") == EXIT["until"]


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_json_carries_the_awaiting_decision_block(
    env: Path, replays: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _new_matter()
    _run(replays)
    capsys.readouterr()

    assert main(["status", "--matter", "demo", "--json"]) == EXIT["ok"]
    report = json.loads(capsys.readouterr().out)

    awaiting = report["awaiting_decision"]
    assert awaiting["gate"] == "1"
    assert awaiting["options"] == ["accept", "reject"]
    assert awaiting["brief"].endswith("briefs/gate-1.md")
    assert awaiting["show_the_user"]
    assert awaiting["how_to_answer"] == (
        "uv run ipfe record-decision --matter demo --gate 1 --decision accept --by operator"
    )
    assert awaiting["then"] == "uv run ipfe run --matter demo"
    assert report["status"] == "awaiting_gate"
    assert report["phases"]["P1"]["status"] == "complete"


def test_status_without_a_pending_gate_has_no_decision_block(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _new_matter()
    capsys.readouterr()
    main(["status", "--matter", "demo", "--json"])
    assert "awaiting_decision" not in json.loads(capsys.readouterr().out)


def test_status_renders_a_human_table(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _new_matter()
    capsys.readouterr()
    main(["status", "--matter", "demo"])
    out = capsys.readouterr().out
    assert "phase    agent" in out
    assert "P1       ip-disclosure" in out
    assert "inner loop  round 0/3" in out


# ---------------------------------------------------------------------------
# record-decision
# ---------------------------------------------------------------------------


def test_decisions_are_idempotent(
    env: Path, replays: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _new_matter()
    _run(replays)
    argv = [
        "record-decision",
        "--matter",
        "demo",
        "--gate",
        "1",
        "--decision",
        "accept",
        "--by",
        "ahmed",
    ]

    assert main(argv) == EXIT["ok"]
    capsys.readouterr()
    assert main(argv) == EXIT["ok"]
    assert "already decided" in capsys.readouterr().out


def test_contradicting_a_decision_exits_2(env: Path, replays: Path) -> None:
    _new_matter()
    _run(replays)
    main(
        [
            "record-decision",
            "--matter",
            "demo",
            "--gate",
            "1",
            "--decision",
            "accept",
            "--by",
            "ahmed",
        ]
    )
    assert (
        main(
            [
                "record-decision",
                "--matter",
                "demo",
                "--gate",
                "1",
                "--decision",
                "reject",
                "--by",
                "ahmed",
            ]
        )
        == EXIT["usage"]
    )


def test_deciding_a_gate_that_is_not_pending_exits_6(env: Path) -> None:
    _new_matter()
    assert (
        main(
            [
                "record-decision",
                "--matter",
                "demo",
                "--gate",
                "2",
                "--decision",
                "accept",
                "--by",
                "ahmed",
            ]
        )
        == EXIT["precondition"]
    )


def test_an_agent_may_not_decide_gate_3(env: Path, replays: Path) -> None:
    _new_matter()
    _run(replays)
    assert (
        main(
            [
                "record-decision",
                "--matter",
                "demo",
                "--gate",
                "1",
                "--decision",
                "accept",
                "--by",
                "patentcounsel",
            ]
        )
        == EXIT["usage"]
    )


# ---------------------------------------------------------------------------
# inspection commands
# ---------------------------------------------------------------------------


def test_prompt_assembles_without_a_run(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _new_matter()
    capsys.readouterr()
    assert main(["prompt", "--matter", "demo", "--prompt", "01"]) == EXIT["ok"]

    out = capsys.readouterr().out
    assert "## Task" in out
    assert "## Dossier files" in out
    assert "00-intake/disclosure.md" in out


def test_prompt_for_an_unknown_id_exits_2(env: Path) -> None:
    _new_matter()
    assert main(["prompt", "--matter", "demo", "--prompt", "99"]) == EXIT["usage"]


def test_show_and_repair_state(
    env: Path, replays: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _new_matter()
    _run(replays)
    capsys.readouterr()

    assert main(["show", "--matter", "demo", "state"]) == EXIT["ok"]
    assert json.loads(capsys.readouterr().out)["matter"]["slug"] == "demo"

    assert main(["show", "--matter", "demo", "log", "--tail", "500"]) == EXIT["ok"]
    assert "run_started" in capsys.readouterr().out

    assert main(["repair-state", "--matter", "demo"]) == EXIT["ok"]
    out = capsys.readouterr().out
    assert "P1       complete" in out
    # The repair keeps what disk cannot prove: the human's gate decision.
    assert json.loads((env / "demo" / "state.json").read_text())["gates"]["1"]["status"] == (
        "pending"
    )


def test_verify_terminology_before_the_lock_is_a_precondition_failure(env: Path) -> None:
    _new_matter()
    assert main(["verify-terminology", "--matter", "demo"]) == EXIT["precondition"]


def test_package_before_one_exists_is_a_precondition_failure(env: Path) -> None:
    _new_matter()
    assert main(["package", "--matter", "demo"]) == EXIT["precondition"]


def test_fake_transport_can_come_from_the_environment(
    env: Path, replays: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _new_matter()
    monkeypatch.setenv("IPFE_TRANSPORT", f"fake:{replays}")
    assert main(["run", "--matter", "demo"]) == EXIT["gate"]


def test_fake_transport_without_fixtures_is_refused(env: Path) -> None:
    _new_matter()
    assert main(["run", "--matter", "demo", "--transport", "fake"]) == EXIT["usage"]


def test_lock_held_by_a_live_process_exits_7(env: Path, replays: Path) -> None:
    from ecosystem.state import Lock

    _new_matter()
    with Lock(env / "demo"):
        assert _run(replays) == EXIT["lock"]


def test_resume_is_an_alias_for_run(env: Path, replays: Path) -> None:
    _new_matter()
    assert (
        main(["resume", "--matter", "demo", "--transport", "fake", "--fixtures", str(replays)])
        == EXIT["gate"]
    )


def test_run_resumes_after_a_transport_failure(
    env: Path, replays: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed call (e.g. an expired provider login) must not strand the matter.

    Re-running after the cause is fixed continues from the cursor and reaches
    Gate 1; it does not need the dossier to be recreated.
    """
    from ecosystem.transport import GatewayAuthError
    from tests import fakes

    _new_matter()

    def expired(self: object, req: object) -> object:
        raise GatewayAuthError("Failed to authenticate: OAuth session expired")

    with monkeypatch.context() as m:
        m.setattr(fakes.FakeTransport, "call", expired)
        assert _run(replays) == EXIT["transport"]
    state = json.loads((env / "demo" / "state.json").read_text())
    assert state["status"] == "failed"
    assert "OAuth" in state["last_error"]["message"]
    # Same matter, working transport: resumes and stops at Gate 1.
    assert _run(replays) == EXIT["gate"]
    state = json.loads((env / "demo" / "state.json").read_text())
    assert state["status"] == "awaiting_gate"
    assert state["last_error"] is None


def test_intake_writes_disclosure_and_named_references(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    assert main(["new-matter", "--slug", "demo", "--title", "T", "--jurisdictions", "US"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("# My invention\n\nIt does a thing."))
    assert main(["intake", "--matter", "demo", "--kind", "disclosure", "--stdin"]) == 0
    disclosure = env / "demo" / "00-intake" / "disclosure.md"
    assert disclosure.read_text().startswith("# My invention")
    # Refuses to overwrite without --force.
    monkeypatch.setattr("sys.stdin", io.StringIO("replacement"))
    assert main(["intake", "--matter", "demo", "--kind", "disclosure", "--stdin"]) == EXIT["usage"]
    assert disclosure.read_text().startswith("# My invention")
    # Directory kinds need a name and get a slugified file.
    monkeypatch.setattr("sys.stdin", io.StringIO("ref text"))
    assert main(["intake", "--matter", "demo", "--kind", "prior-art", "--stdin"]) == EXIT["usage"]
    monkeypatch.setattr("sys.stdin", io.StringIO("ref text"))
    assert (
        main(
            [
                "intake",
                "--matter",
                "demo",
                "--kind",
                "prior-art",
                "--stdin",
                "--name",
                "US 1,234 B2",
            ]
        )
        == 0
    )
    assert (env / "demo" / "00-intake" / "prior-art" / "us-1-234-b2.md").read_text() == "ref text\n"


def test_run_background_detaches_and_reaches_gate_1(env: Path, replays: Path) -> None:
    _new_matter()
    code = main(
        [
            "run",
            "--matter",
            "demo",
            "--background",
            "--transport",
            "fake",
            "--fixtures",
            str(replays),
            "--until",
            "gate1",
        ]
    )
    assert code == 0
    assert (env / "demo" / "run.pid").exists()
    deadline = time.monotonic() + 90
    status = None
    while time.monotonic() < deadline:
        status = json.loads((env / "demo" / "state.json").read_text())["status"]
        if status in {"awaiting_gate", "failed", "complete"}:
            break
        time.sleep(0.5)
    assert status == "awaiting_gate", (env / "demo" / "run.log").read_text()[-2000:]


def test_unattended_run_goes_straight_to_gate_3(env: Path, replays: Path) -> None:
    """Gates 1 and 2 are auto-accepted and recorded as such; Gate 3 still stops."""
    _new_matter()
    assert _run(replays, "--unattended") == EXIT["gate"]
    state = json.loads((env / "demo" / "state.json").read_text())
    assert state["unattended"] is True
    assert state["gates"]["1"]["decided_by"] == "auto-unattended"
    assert state["gates"]["2"]["decided_by"] == "auto-unattended"
    assert state["gates"]["3"]["status"] == "pending"
    package = env / "demo" / "07-package"
    gate_file = next(package.rglob("gate-decisions.md"))
    assert "unattended mode" in gate_file.read_text()
    # A human still owns Gate 3; "auto-unattended" is refused as a signatory.
    assert (
        main(
            [
                "record-decision",
                "--matter",
                "demo",
                "--gate",
                "3",
                "--decision",
                "accept",
                "--by",
                "counsel",
            ]
        )
        == 0
    )
    assert json.loads((env / "demo" / "state.json").read_text())["status"] == "complete"


def test_search_opt_in_is_remembered_and_clearable(env: Path, replays: Path) -> None:
    _new_matter()
    assert _run(replays, "--search", "--until", "gate1") == EXIT["gate"]
    assert json.loads((env / "demo" / "state.json").read_text())["search"] is True
    assert (
        main(
            [
                "record-decision",
                "--matter",
                "demo",
                "--gate",
                "1",
                "--decision",
                "accept",
                "--by",
                "op",
            ]
        )
        == 0
    )
    # Clearing it means Phase 2 runs on human references only (the replay has no search fixtures).
    assert _run(replays, "--no-search", "--until", "gate2") == EXIT["gate"]
    assert json.loads((env / "demo" / "state.json").read_text())["search"] is False
