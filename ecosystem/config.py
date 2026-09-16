"""Settings and the constants the whole pipeline agrees on.

Everything configurable is read from `IPFE_*` environment variables exactly
once, at `Settings.from_env()`. Modules take a `Settings` argument rather than
reaching for a global, so tests can point a whole run at a temp directory
without monkeypatching os.environ.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AGENT_IDS",
    "FINDING_CATEGORIES",
    "PHASES",
    "PHASE_AGENT",
    "Settings",
    "get_settings",
    "phase_dir_name",
]

# The seven native OpenClaw agents (Part 1 of the plan). Order is the pipeline order.
AGENT_IDS: Final[tuple[str, ...]] = (
    "ip-orchestrator",
    "ip-disclosure",
    "ip-prior-art",
    "ip-embodiment",
    "ip-spec",
    "ip-claims",
    "ip-reviewer",
)

# Phase id -> dossier directory name. The numeric prefix keeps `ls` in pipeline order.
PHASES: Final[dict[str, str]] = {
    "P1": "01-disclosure",
    "P2": "02-prior-art",
    "P3": "03-embodiments",
    "P4": "04-specification",
    "P5": "05-claims",
    "REVIEW": "06-review",
    "PACKAGE": "07-package",
}

# Which agent owns each phase. One owner per phase: nothing else may write there.
PHASE_AGENT: Final[dict[str, str]] = {
    "P1": "ip-disclosure",
    "P2": "ip-prior-art",
    "P3": "ip-embodiment",
    "P4": "ip-spec",
    "P5": "ip-claims",
    "REVIEW": "ip-reviewer",
    "PACKAGE": "ip-orchestrator",
}

# Loop-back routing keys. A finding carrying any other category is a contract error.
FINDING_CATEGORIES: Final[list[str]] = [
    "novelty",
    "enablement",
    "design-around",
    "support",
    "formal",
]

_PACKAGE_DIR: Final[Path] = Path(__file__).resolve().parent
_DEFAULT_OPENCLAW_BIN: Final[str] = "~/.openclaw/bin/openclaw"


def phase_dir_name(phase: str) -> str:
    """Directory name for a phase id, raising on an unknown id rather than guessing."""
    try:
        return PHASES[phase]
    except KeyError:
        raise ValueError(f"unknown phase {phase!r}; expected one of {', '.join(PHASES)}") from None


class Settings(BaseModel):
    """Runtime configuration. Immutable once built - a run must not change its own limits."""

    model_config = ConfigDict(frozen=True)

    repo_root: Path = Field(default_factory=lambda: _PACKAGE_DIR.parent)
    dossiers_dir: Path | None = None
    prompts_dir: Path | None = None

    openclaw_bin: str = _DEFAULT_OPENCLAW_BIN

    # Assembly budget. A prompt over this is a design error in the input list,
    # not something to silently truncate.
    max_prompt_chars: int = 600_000
    max_input_chars: int = 250_000

    # Transport selection. `gateway call` carries params in argv (macOS ARG_MAX
    # is 1 MiB); `agent --message-file` is documented at 4 MiB.
    gateway_call_max_bytes: int = 400_000
    agent_cli_max_bytes: int = 4_194_304

    default_timeout_s: int = 1200

    inner_loop_cap: int = 3
    loopback_cap: int = 2
    formal_fix_cap: int = 2
    gate2_reconfirm_on_loopback: bool = True

    terminology_policy: Literal["block", "warn"] = "block"

    def model_post_init(self, _context: object) -> None:
        # Derived paths follow repo_root unless explicitly overridden, so pointing
        # repo_root at a temp dir relocates an entire run.
        root = Path(self.repo_root).expanduser().resolve()
        object.__setattr__(self, "repo_root", root)
        if self.dossiers_dir is None:
            object.__setattr__(self, "dossiers_dir", root / "dossiers")
        else:
            object.__setattr__(self, "dossiers_dir", Path(self.dossiers_dir).expanduser())
        if self.prompts_dir is None:
            object.__setattr__(self, "prompts_dir", root / "prompts")
        else:
            object.__setattr__(self, "prompts_dir", Path(self.prompts_dir).expanduser())

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, **overrides: object) -> Settings:
        """Build from `IPFE_*` variables. Unset variables keep the declared default."""
        source = os.environ if env is None else env
        values: dict[str, object] = {}
        for name, field in cls.model_fields.items():
            raw = source.get(f"IPFE_{name.upper()}")
            if raw is None or raw == "":
                continue
            values[name] = _coerce(name, raw, field.annotation)
        values.update(overrides)
        return cls(**values)

    @property
    def prompts_path(self) -> Path:
        """`prompts_dir` as a plain Path.

        `model_post_init` always fills it in; the field stays `| None` only so
        "unset means derive from repo_root" is expressible. Callers should not
        have to restate that invariant at every use.
        """
        assert self.prompts_dir is not None
        return self.prompts_dir

    @property
    def dossiers_path(self) -> Path:
        """`dossiers_dir` as a plain Path. Same reasoning as `prompts_path`."""
        assert self.dossiers_dir is not None
        return self.dossiers_dir

    def dossier_root(self, slug: str) -> Path:
        """Path of one matter's dossier. Slug validity is the CLI's business."""
        return self.dossiers_path / slug

    def resolve_openclaw_bin(self) -> str:
        """Locate the CLI.

        Prefer the configured path, then PATH. A supervised or non-interactive
        shell frequently lacks `~/.openclaw/bin` on PATH, which is why the
        absolute path is the default rather than the fallback.
        """
        configured = Path(self.openclaw_bin).expanduser()
        if configured.is_file():
            return str(configured)
        if "/" in self.openclaw_bin and self.openclaw_bin != _DEFAULT_OPENCLAW_BIN:
            # An explicitly configured path that does not exist is a
            # misconfiguration; silently using some other openclaw would hide it.
            raise FileNotFoundError(f"IPFE_OPENCLAW_BIN points at a missing file: {configured}")
        found = shutil.which(self.openclaw_bin) or shutil.which("openclaw")
        if found:
            return found
        raise FileNotFoundError(
            f"openclaw CLI not found at {configured} or on PATH; set IPFE_OPENCLAW_BIN"
        )


def _coerce(name: str, raw: str, annotation: object) -> object:
    """Env vars are strings; bools and ints need an explicit, loud conversion."""
    if annotation is bool:
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"IPFE_{name.upper()}={raw!r} is not a boolean")
    if annotation is int:
        try:
            return int(raw.replace("_", "").strip())
        except ValueError:
            raise ValueError(f"IPFE_{name.upper()}={raw!r} is not an integer") from None
    return raw


def get_settings(**overrides: object) -> Settings:
    """Convenience constructor. Deliberately uncached: tests build many."""
    return Settings.from_env(None, **overrides)
