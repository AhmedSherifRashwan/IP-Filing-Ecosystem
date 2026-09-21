"""One call: ids, the two correction turns, write-backs, and what blocks a run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecosystem.calls import (
    Caller,
    ContractFailed,
    TerminologyBlocked,
    call_ids,
    section_body,
)
from ecosystem.config import Settings
from ecosystem.dossier import Dossier, split_frontmatter
from ecosystem.log import EventLog
from ecosystem.playbook import Playbook, PromptSpec, load_playbook
from ecosystem.state import MatterState, new_state
from ecosystem.terminology import parse_locked
from tests.fakes import FakeTransport
from tests.replay_builder import REGISTRY_TABLE, TERMINOLOGY_TABLE, drafting_reply


@pytest.fixture
def playbook(settings: Settings) -> Playbook:
    return load_playbook(settings.prompts_path)


@pytest.fixture
def state(dossier: Dossier) -> MatterState:
    return new_state(dossier.slug, "Demo", ["US"])


@pytest.fixture
def replays(tmp_path: Path) -> Path:
    directory = tmp_path / "replays"
    directory.mkdir()
    return directory


def _caller(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> Caller:
    transport = FakeTransport(mode="replay", fixtures_dir=replays)
    return Caller(
        dossier, state, playbook, transport, settings, EventLog(dossier.log_path, dossier.slug)
    )


def _spec(**overrides) -> PromptSpec:
    data = {
        "id": "t1",
        "title": "Test prompt",
        "phase": "P3",
        "agent": "ip-embodiment",
        "kind": "drafting",
        "inputs": [],
        "optional_inputs": [],
        "deliverable_sections": ["Section"],
        "body": "Do the thing.",
        "timeout_s": 60,
    }
    data.update(overrides)
    return PromptSpec.model_validate(data)


def _fixture(replays: Path, key: str, text: str) -> None:
    FakeTransport(fixtures_dir=replays).write_fixture(key, text)


def _version(dossier: Dossier, phase: str = "P3") -> int:
    return dossier.new_version(phase, "initial").v


# ---------------------------------------------------------------------------
# ids
# ---------------------------------------------------------------------------


def test_call_ids_follow_the_plan(dossier: Dossier) -> None:
    call_id, session, idem = call_ids("demo", "P4", "20a", 2)
    assert call_id == "demo-p4-20a-v2-a0"
    assert session == "demo-p4-20a-v2"
    assert idem == "demo:P4:20a:v2:a0"

    corrected, session_again, idem_again = call_ids("demo", "P4", "20a", 2, attempt=1)
    assert corrected == "demo-p4-20a-v2-a1"
    # The correction is the same session and a different turn.
    assert session_again == session
    assert idem_again != idem


def test_slots_keep_repeats_of_one_prompt_apart() -> None:
    first, _, _ = call_ids("demo", "REVIEW", "37", 1)
    recheck, _, _ = call_ids("demo", "REVIEW", "37", 1, slot="recheck")
    assert first != recheck
    assert recheck == "demo-review-37-recheck-v1-a0"


def test_section_body_extracts_an_h3_and_stops_at_the_next_h2() -> None:
    markdown = "### A\n\nalpha\n\n### B\n\nbeta\n\n## Rationale\n\nsecret"
    assert section_body(markdown, "A") == "alpha"
    assert section_body(markdown, "B") == "beta"
    assert section_body(markdown, "C") == ""


def test_section_body_ignores_headings_inside_code_fences() -> None:
    markdown = "### A\n\n```\n### B\n```\n\ntail"
    assert "### B" in section_body(markdown, "A")
    assert section_body(markdown, "B") == ""


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


def test_a_call_writes_prompt_reply_meta_and_the_output(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    spec = _spec()
    _fixture(replays, "demo-p3-t1-v1", drafting_reply("t1", ["Section"]))
    version = _version(dossier)

    result = _caller(dossier, state, playbook, settings, replays).execute(spec, version)

    assert result.call_id == "demo-p3-t1-v1-a0"
    assert result.output_path is not None
    frontmatter, body = split_frontmatter(result.output_path.read_text(encoding="utf-8"))
    assert frontmatter["agent"] == "ip-embodiment"
    assert frontmatter["call_id"] == result.call_id
    assert frontmatter["model"] == "fake/model"
    assert body.startswith("## Deliverable")
    assert result.envelope is not None and result.envelope.prompt == "t1"
    assert state.agent_runs[-1].status == "ok"
    assert state.counters["calls"] == 1
    assert state.counters["outputs"] == 1


def test_prompt_is_on_disk_before_the_call(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    spec = _spec()
    _fixture(replays, "demo-p3-t1-v1", drafting_reply("t1", ["Section"]))
    caller = _caller(dossier, state, playbook, settings, replays)
    caller.execute(spec, _version(dossier))

    prompt = dossier.runs_dir / "demo-p3-t1-v1-a0" / "prompt.md"
    assert "Do the thing." in prompt.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# contract correction
# ---------------------------------------------------------------------------


def test_one_correction_turn_recovers_a_broken_reply(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    spec = _spec()
    _fixture(replays, "demo-p3-t1-v1.turn1", "## Deliverable\n\nNo envelope, no sections.")
    _fixture(replays, "demo-p3-t1-v1.turn2", drafting_reply("t1", ["Section"]))

    caller = _caller(dossier, state, playbook, settings, replays)
    result = caller.execute(spec, _version(dossier))

    assert result.contract_corrected is True
    assert result.attempts == 2
    assert state.counters["contract_corrections"] == 1
    # The correction turn stays in the same session and gets its own run dir.
    calls = caller.transport.calls  # type: ignore[attr-defined]
    assert calls[0].session_key == calls[1].session_key
    assert calls[0].idempotency_key != calls[1].idempotency_key
    correction = (dossier.runs_dir / "demo-p3-t1-v1-a1" / "prompt.md").read_text(encoding="utf-8")
    assert "Return the complete corrected reply, not a diff." in correction
    assert "## Output contract" in correction
    assert "`## Rationale` section is missing" in correction


def test_a_second_failure_raises_and_keeps_both_replies(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    spec = _spec()
    _fixture(replays, "demo-p3-t1-v1", "## Deliverable\n\nStill broken.")

    with pytest.raises(ContractFailed):
        _caller(dossier, state, playbook, settings, replays).execute(spec, _version(dossier))

    assert (dossier.runs_dir / "demo-p3-t1-v1-a0" / "reply.md").is_file()
    assert (dossier.runs_dir / "demo-p3-t1-v1-a1" / "reply.md").is_file()
    assert state.agent_runs[-1].status == "started"


# ---------------------------------------------------------------------------
# terminology
# ---------------------------------------------------------------------------


def _lock(dossier: Dossier) -> None:
    dossier.write_file(dossier.locked_terminology_path, TERMINOLOGY_TABLE, overwrite=True)
    dossier.write_file(
        dossier.terminology_dir / "LOCKED.json",
        parse_locked(TERMINOLOGY_TABLE).to_json(),
        overwrite=True,
    )


BAD_TERM = "The rubber grommet seals the bore; a needle lifts off it."


def test_a_violation_gets_one_fix_turn_and_then_passes(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    _lock(dossier)
    spec = _spec(phase="P4", agent="ip-spec")
    _fixture(
        replays,
        "demo-p4-t1-v1.turn1",
        drafting_reply("t1", ["Section"], section_text={"Section": BAD_TERM}),
    )
    _fixture(replays, "demo-p4-t1-v1.turn2", drafting_reply("t1", ["Section"]))

    result = _caller(dossier, state, playbook, settings, replays).execute(
        spec, _version(dossier, "P4")
    )

    assert result.terminology_corrected is True
    assert state.counters["terminology_violations"] == 1
    fix = (dossier.runs_dir / "demo-p4-t1-v1-a1" / "prompt.md").read_text(encoding="utf-8")
    assert "rubber grommet" in fix
    assert "preferred: **seat ring**" in fix
    assert result.output_path is not None
    frontmatter, _ = split_frontmatter(result.output_path.read_text(encoding="utf-8"))
    assert frontmatter["status"] == "candidate"


def test_a_persistent_violation_blocks_and_rejects_the_output(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    _lock(dossier)
    spec = _spec(phase="P4", agent="ip-spec")
    _fixture(
        replays,
        "demo-p4-t1-v1",
        drafting_reply("t1", ["Section"], section_text={"Section": BAD_TERM}),
    )

    with pytest.raises(TerminologyBlocked) as excinfo:
        _caller(dossier, state, playbook, settings, replays).execute(spec, _version(dossier, "P4"))

    assert {v.term for v in excinfo.value.violations} >= {"rubber grommet"}
    written = next(dossier.versions("P4")[-1].path.glob("t1-*.md"))
    frontmatter, _ = split_frontmatter(written.read_text(encoding="utf-8"))
    assert frontmatter["status"] == "rejected"
    assert state.terminology.violations_blocked >= 1


def test_terminology_warn_downgrades_the_block(
    dossier: Dossier, state: MatterState, playbook: Playbook, tmp_path: Path, replays: Path
) -> None:
    from tests.conftest import REPO_ROOT

    settings = Settings(
        repo_root=tmp_path, prompts_dir=REPO_ROOT / "prompts", terminology_policy="warn"
    )
    dossier.settings = settings
    _lock(dossier)
    _fixture(
        replays,
        "demo-p3-t1-v1",
        drafting_reply("t1", ["Section"], section_text={"Section": BAD_TERM}),
    )

    caller = _caller(dossier, state, playbook, settings, replays)
    result = caller.execute(_spec(), _version(dossier))
    assert result.output_path is not None
    frontmatter, _ = split_frontmatter(result.output_path.read_text(encoding="utf-8"))
    assert frontmatter["status"] == "candidate"


def test_declared_new_terms_are_drift_not_violations(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    _lock(dossier)
    _fixture(
        replays,
        "demo-p3-t1-v1",
        drafting_reply(
            "t1",
            ["Section"],
            envelope_extra={
                "new_terms": [
                    {
                        "term": "purge shoulder",
                        "maps_to": None,
                        "reason": "a genuinely new component of the poppet",
                    }
                ]
            },
        ),
    )

    _caller(dossier, state, playbook, settings, replays).execute(_spec(), _version(dossier))

    drift = dossier.drift_path.read_text(encoding="utf-8").splitlines()
    assert json.loads(drift[0])["term"] == "purge shoulder"
    assert state.terminology.drift_logged == 1


def test_analysis_prompts_are_not_policed(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    """Analysis prose may discuss the words the drafting must not use."""
    _lock(dossier)
    _fixture(
        replays,
        "demo-p3-t1-v1",
        drafting_reply("t1", ["Section"], section_text={"Section": BAD_TERM}),
    )
    result = _caller(dossier, state, playbook, settings, replays).execute(
        _spec(kind="analysis"), _version(dossier)
    )
    assert result.terminology_corrected is False


# ---------------------------------------------------------------------------
# write-backs
# ---------------------------------------------------------------------------


def test_20a_writes_the_numeral_registry(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    spec = _spec(
        id="20a",
        phase="P4",
        agent="ip-spec",
        deliverable_sections=["Figure Instructions", "Reference-Numeral Registry"],
    )
    _fixture(
        replays,
        "demo-p4-20a-v1",
        drafting_reply(
            "20a",
            ["Figure Instructions", "Reference-Numeral Registry"],
            section_text={"Reference-Numeral Registry": REGISTRY_TABLE},
        ),
    )
    _caller(dossier, state, playbook, settings, replays).execute(spec, _version(dossier, "P4"))

    registry = json.loads(dossier.registry_path.read_text(encoding="utf-8"))
    assert registry["source"] == "20a"
    assert {entry["numeral"] for entry in registry["entries"]} == {
        "10",
        "20",
        "30",
        "40",
        "50",
        "60",
    }


def test_an_unparseable_registry_is_a_contract_failure(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    spec = _spec(id="20a", phase="P4", agent="ip-spec", deliverable_sections=["Registry"])
    _fixture(
        replays,
        "demo-p4-20a-v1",
        drafting_reply("20a", ["Registry"], section_text={"Registry": "No table here."}),
    )
    with pytest.raises(ContractFailed, match="20a"):
        _caller(dossier, state, playbook, settings, replays).execute(spec, _version(dossier, "P4"))


def test_final_claim_set_becomes_claims_md(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    spec = _spec(id="25", phase="P5", agent="ip-claims", deliverable_sections=["Final Claim Set"])
    _fixture(replays, "demo-p5-25-v1", drafting_reply("25", ["Final Claim Set"]))
    version = _version(dossier, "P5")
    _caller(dossier, state, playbook, settings, replays).execute(spec, version)

    claims = (dossier.version("P5", version).path / "claims.md").read_text(encoding="utf-8")
    assert claims.startswith("1. A valve core")
    assert "## Deliverable" not in claims


def test_a_missing_write_back_section_is_a_contract_failure(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    spec = _spec(id="25", phase="P5", agent="ip-claims", deliverable_sections=["Audit"])
    _fixture(replays, "demo-p5-25-v1", drafting_reply("25", ["Audit"]))
    with pytest.raises(ContractFailed, match="Final Claim Set"):
        _caller(dossier, state, playbook, settings, replays).execute(spec, _version(dossier, "P5"))


def test_a_completed_version_is_never_rewritten(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    from ecosystem.dossier import WouldOverwrite

    spec = _spec(id="25", phase="P5", agent="ip-claims", deliverable_sections=["Final Claim Set"])
    _fixture(replays, "demo-p5-25-v1", drafting_reply("25", ["Final Claim Set"]))
    version = _version(dossier, "P5")
    dossier.complete_version("P5", version)

    with pytest.raises(WouldOverwrite, match="complete version"):
        _caller(dossier, state, playbook, settings, replays).execute(spec, version)


# ---------------------------------------------------------------------------
# the checked-in fixtures must stay contract-valid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("demo-p1-04-v1", "analysis"),
        ("demo-review-32-v1", "review"),
        ("demo-package-x-gate-brief-gate-1-v1", "prose"),
    ],
)
def test_checked_in_fixtures_satisfy_their_contract(name: str, kind: str) -> None:
    from ecosystem.contracts import parse_reply
    from tests.conftest import REPO_ROOT

    fixture = json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "replays" / f"{name}.json").read_text(encoding="utf-8")
    )
    text = fixture["response"]["result"]["payloads"][0]["text"]
    parsed = parse_reply(text, kind)
    assert parsed.body_md
    if kind != "prose":
        assert parsed.envelope is not None


def test_analysis_prompts_request_the_tier_model(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    from tests.fakes import FakeTransport

    _lock(dossier)
    spec = _spec(id="06", phase="P2", agent="ip-prior-art", kind="analysis")
    _fixture(replays, "demo-p2-06-v1", drafting_reply("06", ["Section"]))
    transport = FakeTransport(fixtures_dir=replays)
    Caller(
        dossier, state, playbook, transport, settings, EventLog(dossier.log_path, "demo")
    ).execute(spec, _version(dossier, "P2"))
    assert transport.calls[-1].model == "openai/gpt-5.6-sol"
    assert state.agent_runs[-1].requested_model == "openai/gpt-5.6-sol"


def test_no_fix_turn_where_the_lock_only_warns(
    dossier: Dossier, state: MatterState, playbook: Playbook, settings: Settings, replays: Path
) -> None:
    """Phase 3 violations are drift: one call, no correction turn, no exception."""
    from tests.fakes import FakeTransport

    _lock(dossier)
    spec = _spec(phase="P3", agent="ip-embodiment")
    _fixture(
        replays,
        "demo-p3-t1-v1",
        drafting_reply("t1", ["Section"], section_text={"Section": BAD_TERM}),
    )
    transport = FakeTransport(fixtures_dir=replays)
    result = Caller(
        dossier, state, playbook, transport, settings, EventLog(dossier.log_path, "demo")
    ).execute(spec, _version(dossier))
    assert len(transport.calls) == 1
    assert result.attempts == 1
