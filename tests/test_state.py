from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ecosystem import state as st
from ecosystem.config import FINDING_CATEGORIES, PHASES
from ecosystem.dossier import Dossier
from tests.conftest import write_version


def test_new_state_initialises_every_phase_gate_and_counter() -> None:
    state = st.new_state("demo", "Demo matter", ["US", "EP"])

    assert state.schema_version == 1
    assert set(state.phases) == set(PHASES)
    assert state.phases["P4"].agent == "ip-spec"
    assert set(state.gates) == {"1", "2", "3"}
    assert state.loopback.cycles == dict.fromkeys(FINDING_CATEGORIES, 0)
    assert state.status == "idle"


def test_round_trip_preserves_everything(tmp_path: Path) -> None:
    state = st.new_state("demo", "Demo", ["US"])
    state.status = "awaiting_gate"
    state.cursor = st.Cursor(phase="P1", step="04", inner_round=0, call_id="demo-p1-04-v1-a0")
    state.phases["P1"].versions.append(
        st.VersionRecord(v=1, dir="v1-2026-09-16", trigger="initial", prompts_done=["01", "02"])
    )
    state.phases["P1"].current_version = 1
    state.gates["1"].status = "pending"
    state.loopback.cycles["novelty"] = 1
    state.agent_runs.append(st.AgentRun(call_id="c1", agent_id="ip-disclosure", phase="P1"))

    path = tmp_path / "state.json"
    st.save(state, path)
    loaded = st.load(path)

    assert loaded.model_dump() == st.load(path).model_dump()
    assert loaded.cursor.step == "04"
    assert loaded.phases["P1"].versions[0].prompts_done == ["01", "02"]
    assert loaded.gates["1"].status == "pending"
    assert loaded.loopback.cycles["novelty"] == 1
    assert loaded.agent_runs[0].call_id == "c1"


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    st.save(st.new_state("demo", "Demo", []), path)
    first = path.read_text()

    state = st.load(path)
    state.status = "running"
    st.save(state, path)

    assert json.loads(path.read_text())["status"] == "running"
    assert first != path.read_text()
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_save_failure_leaves_previous_state_intact(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state.json"
    st.save(st.new_state("demo", "Demo", []), path)
    original = path.read_text()

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(st.os, "replace", boom)
    with pytest.raises(OSError):
        st.save(st.new_state("demo", "Other", []), path)

    assert path.read_text() == original
    assert list(tmp_path.glob(".state-*")) == []


def test_load_rejects_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(st.StateError):
        st.load(path)

    path.write_text('{"schema_version": 1}')
    with pytest.raises(st.StateError):
        st.load(path)


def test_gate_accessor_creates_loopback_cap_gates_only(tmp_path: Path) -> None:
    state = st.new_state("demo", "Demo", [])
    gate = state.gate("loopback-cap-novelty")
    gate.status = "pending"

    assert state.gates["loopback-cap-novelty"].status == "pending"
    with pytest.raises(st.StateError):
        state.gate("7")


# -- lock -------------------------------------------------------------------


def test_lock_is_exclusive_while_held(tmp_path: Path) -> None:
    with st.Lock(tmp_path) as lock:
        assert lock.path.is_file()
        with pytest.raises(st.LockHeld) as excinfo:
            st.Lock(tmp_path).acquire()
        assert excinfo.value.pid == os.getpid()
    assert not (tmp_path / "state.lock").exists()


def test_stale_lock_from_a_dead_pid_is_taken_over(tmp_path: Path) -> None:
    dead = _dead_pid()
    (tmp_path / "state.lock").write_text(json.dumps({"pid": dead, "at": "x"}))

    with st.Lock(tmp_path) as lock:
        assert json.loads(lock.path.read_text())["pid"] == os.getpid()
    assert not (tmp_path / "state.lock").exists()


def test_garbage_lock_file_is_replaced(tmp_path: Path) -> None:
    (tmp_path / "state.lock").write_text("not json at all")
    with st.Lock(tmp_path):
        pass
    assert not (tmp_path / "state.lock").exists()


def test_release_does_not_remove_another_owners_lock(tmp_path: Path) -> None:
    lock = st.Lock(tmp_path).acquire()
    (tmp_path / "state.lock").write_text(json.dumps({"pid": _dead_pid()}))
    lock.release()
    assert (tmp_path / "state.lock").is_file()


def _dead_pid() -> int:
    """A pid that is free right now. Fork-free: search downward from a high value."""
    for candidate in range(99_999, 90_000, -1):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except PermissionError:
            continue
    raise RuntimeError("no free pid found")


# -- repair -----------------------------------------------------------------


def test_repair_from_disk_rebuilds_phases_and_runs(dossier: Dossier) -> None:
    write_version(dossier, "P1", {"01": "a", "02": "b"})
    write_version(dossier, "P1", {"01": "c"}, trigger="manual-fix")
    running = dossier.new_version("P3", "initial")
    run_dir = dossier.run_dir("demo-p1-01-v1-a0")
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "call_id": "demo-p1-01-v1-a0",
                "agent_id": "ip-disclosure",
                "phase": "P1",
                "prompt": "01",
                "status": "ok",
                "duration_ms": 900,
            }
        )
    )

    state = st.repair_from_disk(dossier.root)

    assert [v.v for v in state.phases["P1"].versions] == [1, 2]
    assert state.phases["P1"].current_version == 2
    assert state.phases["P1"].status == "complete"
    assert state.phases["P1"].versions[1].trigger == "manual-fix"
    assert state.phases["P3"].status == "running"
    assert state.phases["P3"].current_version == running.v
    assert state.phases["P2"].status == "idle"
    assert [r.call_id for r in state.agent_runs] == ["demo-p1-01-v1-a0"]
    assert state.counters["calls"] == 1


def test_repair_keeps_human_decisions_and_prompts_done(dossier: Dossier) -> None:
    state = st.new_state("demo", "Demo", ["US"])
    state.gates["1"].status = "decided"
    state.gates["1"].decided_by = "ahmed"
    state.loopback.cycles["novelty"] = 2
    v = write_version(dossier, "P1", {"01": "a"})
    state.phases["P1"].versions.append(
        st.VersionRecord(v=v, dir=f"v{v}-x", trigger="initial", prompts_done=["01"])
    )
    st.save(state, dossier.state_path)

    repaired = st.repair_from_disk(dossier.root)

    assert repaired.gates["1"].decided_by == "ahmed"
    assert repaired.loopback.cycles["novelty"] == 2
    assert repaired.phases["P1"].versions[0].prompts_done == ["01"]


def test_repair_skips_malformed_version_json(dossier: Dossier) -> None:
    write_version(dossier, "P1", {"01": "a"})
    bad = dossier.new_version("P1", "initial")
    bad.version_json.write_text("{{{")

    state = st.repair_from_disk(dossier.root)

    assert [v.v for v in state.phases["P1"].versions] == [1]


def test_repair_without_state_json_falls_back_to_matter_json(dossier: Dossier) -> None:
    dossier.state_path.unlink(missing_ok=True)
    state = st.repair_from_disk(dossier.root)
    assert state.matter.slug == "demo"
    assert state.matter.jurisdictions == ["US", "EP"]
