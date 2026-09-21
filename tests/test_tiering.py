"""Analysis prompts run on the analysis-tier model; drafting stays on the agent's model."""

from __future__ import annotations

from pathlib import Path

from ecosystem.config import ANALYSIS_TIER_PROMPTS, Settings
from ecosystem.transport import AgentCliBackend, AutoTransport, CallRequest


def _req(model: str | None, tmp_path: Path) -> CallRequest:
    return CallRequest(
        agent_id="ip-prior-art",
        prompt_path=tmp_path / "p.md",
        prompt_text="hello",
        session_key="agent:ip-prior-art:demo-p2-06-v1",
        idempotency_key="demo:P2:06:v1:a0",
        timeout_s=10,
        model=model,
    )


def test_tier_mapping_defaults_and_overrides() -> None:
    s = Settings()
    assert s.model_for_prompt("06") == "openai/gpt-5.6-sol"
    assert s.model_for_prompt("22") is None
    assert (
        s.model_for_prompt("22", explicit="anthropic/claude-sonnet-5")
        == "anthropic/claude-sonnet-5"
    )
    assert "x-search-triage" in ANALYSIS_TIER_PROMPTS
    assert Settings(model_analysis="none").model_for_prompt("06") is None
    assert Settings(analysis_prompts="22,23").model_for_prompt("22") == "openai/gpt-5.6-sol"
    assert Settings(analysis_prompts="22,23").model_for_prompt("06") is None


def test_agent_cli_argv_carries_the_model_and_retry_keeps_it(tmp_path: Path) -> None:
    (tmp_path / "p.md").write_text("hello")
    req = _req("openai/gpt-5.6-sol", tmp_path)
    argv = AgentCliBackend("/fake/openclaw").build_argv(req)
    assert argv[argv.index("--model") + 1] == "openai/gpt-5.6-sol"
    assert req.with_retry(1).model == "openai/gpt-5.6-sol"
    assert "--model" not in AgentCliBackend("/fake/openclaw").build_argv(_req(None, tmp_path))


def test_auto_transport_uses_the_cli_backend_for_tiered_calls(tmp_path: Path) -> None:
    (tmp_path / "p.md").write_text("hello")
    auto = AutoTransport(Settings(), bin_path="/fake/openclaw")
    assert auto.select(_req("openai/gpt-5.6-sol", tmp_path)).name == "agent-cli"
    assert auto.select(_req(None, tmp_path)).name == "gateway-call"
