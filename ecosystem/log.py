"""Append-only JSONL event log, one file per matter.

The log is the forensic record of a run: what was called, what came back, which
gate stopped it. It is append-only and never rewritten, so a crashed run still
explains itself. `state.json` is the current truth; `log.jsonl` is the history.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

log = logging.getLogger("ipfe")

__all__ = ["EVENTS", "EventLog", "utc_now"]

# The vocabulary of plan s3.5. Kept as a set so a typo in an event name is
# visible in the logs instead of silently creating a new event type.
EVENTS: Final[frozenset[str]] = frozenset(
    {
        "matter_created",
        "run_started",
        "phase_started",
        "call_started",
        "call_finished",
        "call_failed",
        "contract_corrected",
        "contract_failed",
        "output_written",
        "terminology_violation",
        "terminology_drift",
        "gate_requested",
        "gate_decided",
        "inner_loop_round",
        "loopback_routed",
        "loopback_cap_reached",
        "package_built",
        "run_stopped",
        "run_failed",
        "phase_completed",
        "intake_written",
        "notify_failed",
        "search_completed",
        "search_unavailable",
    }
)

# Promoted out of `detail` into the record's top level: these are the fields
# every consumer filters on.
_TOP_LEVEL: Final[tuple[str, ...]] = ("phase", "prompt", "call_id")


def utc_now() -> str:
    """ISO-8601 UTC with a `Z`-equivalent offset. One timestamp format everywhere."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class EventLog:
    """Writer/reader for one `log.jsonl`."""

    def __init__(self, path: Path | str, matter: str = "") -> None:
        self.path = Path(path)
        self.matter = matter
        self._observers: list[Callable[[dict[str, Any]], None]] = []
        self._write_lock = threading.Lock()

    def subscribe(self, observer: Callable[[dict[str, Any]], None]) -> None:
        """Register a callback that sees every record after it is written.

        Observers are how a run pushes progress to a human (see `notify.py`)
        without the runner knowing anything about chat sessions. An observer
        that raises is logged and dropped from the call, never re-raised: a
        broken notifier must not abort a run that is otherwise fine.
        """
        self._observers.append(observer)

    def emit(self, event: str, **detail: Any) -> dict[str, Any]:
        """Append one event. Returns the written record so callers can echo it.

        Never raises on a write failure being impossible to recover from - a
        broken log must not abort a run that is otherwise fine - but it does say
        so through the stdlib logger.
        """
        if event not in EVENTS:
            log.warning("unknown log event %r (writing it anyway)", event)

        record: dict[str, Any] = {
            "ts": utc_now(),
            "event": event,
            "matter": detail.pop("matter", self.matter),
        }
        for key in _TOP_LEVEL:
            value = detail.pop(key, None)
            if value is not None:
                record[key] = value
        record["detail"] = _jsonable(detail)

        line = json.dumps(record, ensure_ascii=False, default=str)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._write_lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:  # pragma: no cover - disk-full class of failure
            log.error("event log write failed for %s: %s", self.path, exc)
        for observer in list(self._observers):
            try:
                observer(record)
            except Exception as exc:  # deliberately broad: observers are never load-bearing
                log.warning("event observer failed on %s: %s", event, exc)
        return record

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        """Last `n` well-formed records, oldest first. Corrupt lines are skipped."""
        if n <= 0 or not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, Any]] = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping malformed log line in %s", self.path)
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        return records


def _jsonable(value: Any) -> Any:
    """Paths and pydantic models appear in details constantly; make them safe to dump."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    return value
