from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecosystem.config import AGENT_IDS, PHASE_AGENT, PHASES, Settings, phase_dir_name
from ecosystem.log import EventLog


def test_defaults_derive_from_repo_root(tmp_path: Path) -> None:
    settings = Settings(repo_root=tmp_path)

    assert settings.dossiers_dir == tmp_path / "dossiers"
    assert settings.prompts_dir == tmp_path / "prompts"
    assert settings.dossier_root("acme") == tmp_path / "dossiers" / "acme"
    assert settings.max_prompt_chars == 600_000
    assert settings.gateway_call_max_bytes == 400_000
    assert settings.agent_cli_max_bytes == 4_194_304
    assert settings.inner_loop_cap == 3
    assert settings.loopback_cap == 2
    assert settings.terminology_policy == "block"
    assert settings.openclaw_bin == "~/.openclaw/bin/openclaw"


def test_settings_are_frozen(tmp_path: Path) -> None:
    settings = Settings(repo_root=tmp_path)
    with pytest.raises(ValidationError):
        settings.inner_loop_cap = 9  # type: ignore[misc]


def test_from_env_reads_ipfe_variables(tmp_path: Path) -> None:
    env = {
        "IPFE_REPO_ROOT": str(tmp_path),
        "IPFE_MAX_PROMPT_CHARS": "123_000",
        "IPFE_GATE2_RECONFIRM_ON_LOOPBACK": "false",
        "IPFE_TERMINOLOGY_POLICY": "warn",
        "IPFE_DOSSIERS_DIR": str(tmp_path / "elsewhere"),
        "OTHER": "ignored",
    }

    settings = Settings.from_env(env)

    assert settings.repo_root == tmp_path.resolve()
    assert settings.max_prompt_chars == 123_000
    assert settings.gate2_reconfirm_on_loopback is False
    assert settings.terminology_policy == "warn"
    assert settings.dossiers_dir == tmp_path / "elsewhere"
    assert settings.prompts_dir == tmp_path.resolve() / "prompts"


def test_from_env_rejects_nonsense(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not an integer"):
        Settings.from_env({"IPFE_INNER_LOOP_CAP": "three"})
    with pytest.raises(ValueError, match="not a boolean"):
        Settings.from_env({"IPFE_GATE2_RECONFIRM_ON_LOOPBACK": "maybe"})
    with pytest.raises(ValueError):
        Settings.from_env({"IPFE_TERMINOLOGY_POLICY": "shout"})


def test_resolve_openclaw_bin_fails_loudly(tmp_path: Path) -> None:
    settings = Settings(repo_root=tmp_path, openclaw_bin=str(tmp_path / "nope"))
    with pytest.raises(FileNotFoundError, match="missing file"):
        settings.resolve_openclaw_bin()

    real = tmp_path / "openclaw"
    real.write_text("#!/bin/sh\n")
    settings = Settings(repo_root=tmp_path, openclaw_bin=str(real))
    assert settings.resolve_openclaw_bin() == str(real)


def test_constants_are_consistent() -> None:
    assert len(AGENT_IDS) == 7
    assert set(PHASE_AGENT) == set(PHASES)
    assert set(PHASE_AGENT.values()) <= set(AGENT_IDS)
    assert PHASES["REVIEW"] == "06-review"
    assert phase_dir_name("P4") == "04-specification"
    with pytest.raises(ValueError):
        phase_dir_name("P9")


# -- event log --------------------------------------------------------------


def test_emit_writes_one_record_per_line_with_promoted_fields(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "log.jsonl", matter="demo")

    log.emit("run_started", transport="auto")
    record = log.emit(
        "call_finished",
        phase="P1",
        prompt="04",
        call_id="demo-p1-04-v1-a0",
        duration_ms=900,
        path=tmp_path / "out.md",
    )

    lines = (tmp_path / "log.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "run_started"
    assert first["matter"] == "demo"
    assert first["detail"] == {"transport": "auto"}
    assert first["ts"].endswith("+00:00")
    assert "phase" not in first

    second = json.loads(lines[1])
    assert second["phase"] == "P1"
    assert second["prompt"] == "04"
    assert second["call_id"] == "demo-p1-04-v1-a0"
    assert second["detail"]["path"].endswith("out.md")
    assert second == record


def test_unknown_event_is_written_with_a_warning(tmp_path: Path, caplog) -> None:
    log = EventLog(tmp_path / "log.jsonl")
    log.emit("typoed_event")
    assert "unknown log event" in caplog.text
    assert json.loads((tmp_path / "log.jsonl").read_text())["event"] == "typoed_event"


def test_tail_returns_the_last_records_and_skips_corruption(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    log = EventLog(path, matter="demo")
    assert log.tail(5) == []

    for i in range(5):
        log.emit("phase_started", phase=f"P{i}")
    with path.open("a") as handle:
        handle.write("{broken\n")

    tail = log.tail(3)
    assert [r["phase"] for r in tail] == ["P3", "P4"]
    assert log.tail(0) == []
