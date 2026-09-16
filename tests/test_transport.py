from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from ecosystem.config import Settings
from ecosystem.transport import (
    AgentCliBackend,
    AutoTransport,
    CallRequest,
    GatewayAuthError,
    GatewayCallBackend,
    GatewayError,
    GatewayRateLimited,
    GatewayTimeout,
    GatewayTransientError,
    PromptTooLarge,
    assert_session_key_matches,
    call_with_backoff,
    classify_error,
    session_key,
)
from tests.fakes import FakeTransport, make_payload


@dataclass
class Completed:
    stdout: str
    stderr: str = ""
    returncode: int = 0


class StubRunner:
    """Stands in for subprocess.run: records argv, returns queued results."""

    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.argv: list[list[str]] = []
        self.timeouts: list[float] = []

    def __call__(self, argv: list[str], **kwargs: object) -> Completed:
        self.argv.append(argv)
        self.timeouts.append(kwargs.get("timeout"))  # type: ignore[arg-type]
        result = self.results.pop(0) if self.results else Completed(json.dumps(make_payload("ok")))
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, Completed)
        return result


@pytest.fixture
def request_(tmp_path: Path) -> CallRequest:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Draft the claims. Confidential.")
    return CallRequest(
        agent_id="ip-claims",
        prompt_path=prompt,
        prompt_text=prompt.read_text(),
        session_key="agent:ip-claims:demo-p5-22-v1-a0",
        idempotency_key="demo:P5:22:v1:a0",
        timeout_s=900,
    )


# -- session keys -----------------------------------------------------------


def test_session_key_is_lowercase_sanitised_and_bounded() -> None:
    assert session_key("ip-spec", "demo-P4-20a-v1-a0") == "agent:ip-spec:demo-p4-20a-v1-a0"
    assert session_key("ip-spec", "a b/c_d") == "agent:ip-spec:a-b-c-d"
    assert len(session_key("ip-reviewer", "x" * 200)) < 96


def test_assert_session_key_matches_rejects_the_wrong_agent() -> None:
    assert_session_key_matches("ip-claims", "agent:ip-claims:x")
    assert_session_key_matches("ip-claims", None)
    # Not an agent-scoped key: the gateway does not check it, so neither do we.
    assert_session_key_matches("ip-claims", "call:something")
    with pytest.raises(ValueError, match="names agent 'ip-spec'"):
        assert_session_key_matches("ip-claims", "agent:ip-spec:x")


def test_a_backend_refuses_a_mismatched_session_key(request_: CallRequest) -> None:
    runner = StubRunner()
    bad = CallRequest(**{**request_.__dict__, "session_key": "agent:ip-spec:x"})
    with pytest.raises(ValueError):
        GatewayCallBackend("/bin/openclaw", runner=runner).call(bad)
    assert runner.argv == []


# -- argv construction ------------------------------------------------------


def test_gateway_call_argv(request_: CallRequest) -> None:
    runner = StubRunner(Completed(json.dumps(make_payload("reply"))))
    GatewayCallBackend("/bin/openclaw", runner=runner).call(request_)

    argv = runner.argv[0]
    assert argv[:4] == ["/bin/openclaw", "gateway", "call", "agent"]
    assert argv[4] == "--params"
    assert argv[6:] == ["--timeout", "900000", "--json", "--expect-final"]
    assert json.loads(argv[5]) == {
        "agentId": "ip-claims",
        "message": "Draft the claims. Confidential.",
        "idempotencyKey": "demo:P5:22:v1:a0",
        "sessionKey": "agent:ip-claims:demo-p5-22-v1-a0",
        "timeout": 900_000,
    }
    assert runner.timeouts[0] == 960  # timeout_s + 60s grace


def test_agent_cli_argv(request_: CallRequest) -> None:
    runner = StubRunner(Completed(json.dumps(make_payload("reply"))))
    AgentCliBackend("/bin/openclaw", runner=runner).call(request_)

    assert runner.argv[0] == [
        "/bin/openclaw",
        "agent",
        "--agent",
        "ip-claims",
        "--session-key",
        "agent:ip-claims:demo-p5-22-v1-a0",
        "--message-file",
        str(request_.prompt_path),
        "--json",
        "--timeout",
        "900",
    ]


def test_agent_cli_refuses_a_missing_prompt_file(request_: CallRequest, tmp_path: Path) -> None:
    runner = StubRunner()
    missing = CallRequest(**{**request_.__dict__, "prompt_path": tmp_path / "gone.md"})
    with pytest.raises(GatewayError, match="prompt file does not exist"):
        AgentCliBackend("/bin/openclaw", runner=runner).call(missing)
    assert runner.argv == []


# -- reply parsing ----------------------------------------------------------


def test_reply_parsing_concatenates_payloads_and_reads_meta(request_: CallRequest) -> None:
    payload = make_payload("first", run_id="run-7", provider="anthropic", model="claude-opus-5")
    payload["result"]["payloads"].append({"text": "second"})
    runner = StubRunner(Completed("noise before\n" + json.dumps(payload)))

    reply = GatewayCallBackend("/bin/openclaw", runner=runner).call(request_)

    assert reply.text == "first\nsecond"
    assert reply.run_id == "run-7"
    assert reply.status == "ok"
    assert reply.provider == "anthropic"
    assert reply.model == "claude-opus-5"
    assert reply.duration_ms == 1234
    assert reply.transport == "gateway-call"


def test_empty_output_and_unparseable_output_fail(request_: CallRequest) -> None:
    backend = GatewayCallBackend("/bin/openclaw", runner=StubRunner(Completed("", "boom")))
    with pytest.raises(GatewayError, match="boom"):
        backend.call(request_)

    backend = GatewayCallBackend("/bin/openclaw", runner=StubRunner(Completed("not json")))
    with pytest.raises(GatewayError, match="unparseable"):
        backend.call(request_)


def test_no_text_payload_is_an_error(request_: CallRequest) -> None:
    payload = make_payload("x")
    payload["result"]["payloads"] = []
    backend = GatewayCallBackend("/bin/openclaw", runner=StubRunner(Completed(json.dumps(payload))))
    with pytest.raises(GatewayError, match="no text payload"):
        backend.call(request_)


def test_gateway_level_failures_are_classified(request_: CallRequest) -> None:
    failure = {"ok": False, "error": {"message": "429 rate limit exceeded"}}
    backend = GatewayCallBackend("/bin/openclaw", runner=StubRunner(Completed(json.dumps(failure))))
    with pytest.raises(GatewayRateLimited):
        backend.call(request_)

    non_ok = {"runId": "r", "status": "error", "summary": "unauthorized"}
    backend = GatewayCallBackend("/bin/openclaw", runner=StubRunner(Completed(json.dumps(non_ok))))
    with pytest.raises(GatewayAuthError):
        backend.call(request_)


def test_subprocess_timeout_becomes_gateway_timeout(request_: CallRequest) -> None:
    runner = StubRunner(subprocess.TimeoutExpired(cmd="openclaw", timeout=960))
    with pytest.raises(GatewayTimeout):
        GatewayCallBackend("/bin/openclaw", runner=runner).call(request_)


def test_missing_binary_is_reported_clearly(request_: CallRequest) -> None:
    runner = StubRunner(FileNotFoundError())
    with pytest.raises(GatewayError, match="not executable"):
        GatewayCallBackend("/bin/openclaw", runner=runner).call(request_)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Too Many Requests", GatewayRateLimited),
        ("credit balance too low", GatewayRateLimited),
        ("unauthorized", GatewayAuthError),
        ("request timed out", GatewayTransientError),
        ("ECONNREFUSED 127.0.0.1", GatewayTransientError),
        ("turn is in_flight for this key", GatewayTransientError),
        ("became active in another runner", GatewayTransientError),
        ("something odd", GatewayError),
    ],
)
def test_error_classification(message: str, expected: type[Exception]) -> None:
    error = classify_error(message)
    assert type(error) is expected
    assert classify_error("something odd", retryable=True).__class__ is GatewayTransientError


# -- auto selection ---------------------------------------------------------


def auto(tmp_path: Path, runner: StubRunner, **overrides: object) -> AutoTransport:
    settings = Settings(repo_root=tmp_path, **overrides)
    return AutoTransport(settings, runner=runner, bin_path="/bin/openclaw", sleep=lambda _: None)


def _request(tmp_path: Path, size: int) -> CallRequest:
    prompt = tmp_path / "prompt.md"
    text = "x" * size
    prompt.write_text(text)
    return CallRequest(
        agent_id="ip-spec",
        prompt_path=prompt,
        prompt_text=text,
        session_key="agent:ip-spec:demo-p4-16-v1-a0",
        idempotency_key="demo:P4:16:v1:a0",
        timeout_s=1200,
    )


def test_auto_selects_gateway_below_the_threshold_and_cli_above(tmp_path: Path) -> None:
    runner = StubRunner()
    transport = auto(tmp_path, runner, gateway_call_max_bytes=1000, agent_cli_max_bytes=10_000)

    assert transport.select(_request(tmp_path, 100)).name == "gateway-call"
    small = _request(tmp_path, 100)
    boundary = 1000 - (transport.params_bytes(small) - 100)
    assert transport.select(_request(tmp_path, boundary)).name == "gateway-call"
    assert transport.select(_request(tmp_path, boundary + 1)).name == "agent-cli"


def test_auto_refuses_a_prompt_over_the_cli_limit(tmp_path: Path) -> None:
    transport = auto(tmp_path, StubRunner(), gateway_call_max_bytes=100, agent_cli_max_bytes=500)
    with pytest.raises(PromptTooLarge, match="agent-cli limit"):
        transport.call(_request(tmp_path, 600))


def test_forced_transport_is_honoured_and_still_bounded(tmp_path: Path) -> None:
    runner = StubRunner()
    settings = Settings(repo_root=tmp_path, gateway_call_max_bytes=1000, agent_cli_max_bytes=10_000)

    forced_cli = AutoTransport(settings, force="agent-cli", runner=runner, bin_path="/bin/oc")
    assert forced_cli.select(_request(tmp_path, 10)).name == "agent-cli"

    forced_gateway = AutoTransport(
        settings, force="gateway-call", runner=runner, bin_path="/bin/oc"
    )
    with pytest.raises(PromptTooLarge, match="argv limit"):
        forced_gateway.select(_request(tmp_path, 5000))


def test_auto_call_routes_through_the_selected_backend(tmp_path: Path) -> None:
    runner = StubRunner(Completed(json.dumps(make_payload("done"))))
    transport = auto(tmp_path, runner, gateway_call_max_bytes=10, agent_cli_max_bytes=10_000)

    reply = transport.call(_request(tmp_path, 50))

    assert reply.text == "done"
    assert runner.argv[0][1] == "agent"


# -- backoff ----------------------------------------------------------------


class FlakyBackend:
    name = "flaky"

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.seen: list[CallRequest] = []

    def call(self, req: CallRequest):
        self.seen.append(req)
        if len(self.seen) <= self.failures:
            raise GatewayTransientError("turn in_flight")
        return make_reply()


def make_reply():
    from ecosystem.transport import parse_gateway_payload

    return parse_gateway_payload(make_payload("recovered"))


def test_backoff_retries_transient_failures_with_fresh_keys(request_: CallRequest) -> None:
    backend = FlakyBackend(failures=2)
    delays: list[float] = []

    reply = call_with_backoff(backend, request_, sleep=delays.append)

    assert reply.text == "recovered"
    assert [r.session_key for r in backend.seen] == [
        "agent:ip-claims:demo-p5-22-v1-a0",
        "agent:ip-claims:demo-p5-22-v1-a0-r1",
        "agent:ip-claims:demo-p5-22-v1-a0-r2",
    ]
    assert [r.idempotency_key for r in backend.seen] == [
        "demo:P5:22:v1:a0",
        "demo:P5:22:v1:a0-r1",
        "demo:P5:22:v1:a0-r2",
    ]
    assert delays == [6, 12]


def test_backoff_gives_up_after_three_attempts(request_: CallRequest) -> None:
    backend = FlakyBackend(failures=99)
    with pytest.raises(GatewayTransientError):
        call_with_backoff(backend, request_, sleep=lambda _: None)
    assert len(backend.seen) == 3


def test_backoff_does_not_retry_a_permanent_failure(request_: CallRequest) -> None:
    class AuthBackend:
        name = "auth"
        calls = 0

        def call(self, req: CallRequest):
            type(self).calls += 1
            raise GatewayAuthError("unauthorized")

    backend = AuthBackend()
    with pytest.raises(GatewayAuthError):
        call_with_backoff(backend, request_, sleep=lambda _: None)
    assert AuthBackend.calls == 1


def test_retry_keys_stay_within_the_session_key_limit() -> None:
    long = CallRequest(
        agent_id="ip-reviewer",
        prompt_path=Path("/tmp/p.md"),
        prompt_text="x",
        session_key=session_key("ip-reviewer", "y" * 200),
        idempotency_key="k" * 120,
        timeout_s=10,
    )
    assert len(long.with_retry(2).session_key) <= 96


# -- fake transport ---------------------------------------------------------


def test_fake_transport_replays_by_call_id(tmp_path: Path, request_: CallRequest) -> None:
    fake = FakeTransport("replay", tmp_path / "replays")
    fake.write_fixture("demo-p5-22-v1-a0", "## Deliverable\n\nclaims")

    reply = fake.call(request_)

    assert reply.text == "## Deliverable\n\nclaims"
    assert fake.calls == [request_]


def test_fake_transport_falls_back_to_the_prompt_hash(
    tmp_path: Path, request_: CallRequest
) -> None:
    import hashlib

    fake = FakeTransport("replay", tmp_path / "replays")
    digest = hashlib.sha256(request_.prompt_text.encode()).hexdigest()
    fake.write_fixture(digest, "hashed reply")
    unkeyed = CallRequest(**{**request_.__dict__, "session_key": "call:whatever"})

    assert fake.call(unkeyed).text == "hashed reply"


def test_missing_fixture_names_the_key_and_lists_what_exists(
    tmp_path: Path, request_: CallRequest
) -> None:
    fake = FakeTransport("replay", tmp_path / "replays")
    fake.write_fixture("other-call", "x")

    with pytest.raises(AssertionError) as excinfo:
        fake.call(request_)

    assert "demo-p5-22-v1-a0" in str(excinfo.value)
    assert "other-call" in str(excinfo.value)


def test_record_mode_writes_a_replayable_fixture(tmp_path: Path, request_: CallRequest) -> None:
    runner = StubRunner(Completed(json.dumps(make_payload("live reply"))))
    inner = GatewayCallBackend("/bin/openclaw", runner=runner)
    recorder = FakeTransport("record", tmp_path / "replays", inner=inner)

    recorder.call(request_)

    fixture = json.loads((tmp_path / "replays" / "demo-p5-22-v1-a0.json").read_text())
    assert fixture["agent_id"] == "ip-claims"
    assert fixture["session_key"] == request_.session_key
    assert fixture["prompt_sha256"]
    assert FakeTransport("replay", tmp_path / "replays").call(request_).text == "live reply"


def test_record_mode_needs_an_inner_transport(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        FakeTransport("record", tmp_path)
    with pytest.raises(ValueError):
        FakeTransport("sideways", tmp_path)
