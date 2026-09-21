"""The Invention & Drafting Dossier on disk: layout, versioning, input resolution.

Two rules hold the whole design together:

1. **Nothing inside an existing version directory is ever rewritten.** Re-entering
   a phase opens `v<N+1>`; N is never reused, so an artefact referenced by a
   frontmatter sha256 still says what it said when it was referenced. The only
   permitted in-place edit is `mark_status`, which flips `candidate` to
   `confirmed` when a human gate decides.
2. **Cross-call context travels as embedded files, never as session history**
   (playbook R1). `resolve_input` is the single implementation of that grammar,
   shared by the prompt builder and the playbook validator, so a prompt file
   cannot name an input the runner cannot resolve.
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel

from ecosystem.config import FINDING_CATEGORIES, PHASES, Settings, phase_dir_name
from ecosystem.state import new_state
from ecosystem.state import save as save_state

log = logging.getLogger("ipfe")

__all__ = [
    "Dossier",
    "DossierError",
    "InputContext",
    "MissingInput",
    "ResolvedInput",
    "UnknownInputSpec",
    "VersionDir",
    "WouldOverwrite",
    "embed_output_text",
    "render_frontmatter",
    "sha256_file",
    "sha256_text",
    "split_frontmatter",
    "validate_spec",
]


class DossierError(RuntimeError):
    """Base for every dossier-layout failure."""


class WouldOverwrite(DossierError):
    """An artefact already exists. Versions are immutable; open a new one."""


class MissingInput(DossierError):
    """A required input does not exist. The caller's precondition is wrong."""

    def __init__(self, spec: str, detail: str) -> None:
        super().__init__(f"required input {spec!r} is missing: {detail}")
        self.spec = spec
        self.detail = detail


class UnknownInputSpec(DossierError):
    """An input spec that is not in the grammar - almost always a prompt-file typo."""

    def __init__(self, spec: str) -> None:
        super().__init__(f"unknown input spec {spec!r}")
        self.spec = spec


# Package member -> filename inside `package/`.
PACKAGE_MEMBERS: Final[dict[str, str]] = {
    "description": "description.md",
    "figures": "figures.md",
    "claims": "claims.md",
    "registry": "registry.json",
    "abstract": "abstract.md",
}

# Bare specs that take no argument.
_BARE_SPECS: Final[frozenset[str]] = frozenset(
    {
        "confirmed_embodiments",
        "terminology",
        "registry",
        "numeral_crosscheck",
        "corrections",
        "disclosure_requests",
        "open_disagreements",
        "inner_loop_flags",
    }
)

# Sections an embedded earlier output contributes. `## Rationale` is withheld:
# it is drafting reasoning, and Agent 7 must never see it (plan s3.4).
EMBED_SECTIONS: Final[tuple[str, ...]] = ("Deliverable", "Assumptions", "Open Questions")

_VERSION_DIR_RE: Final[re.Pattern[str]] = re.compile(r"^v(\d+)-\d{4}-\d{2}-\d{2}$")
_FRONTMATTER_RE: Final[re.Pattern[str]] = re.compile(
    r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n)*", re.DOTALL
)
_H2_RE: Final[re.Pattern[str]] = re.compile(r"^##\s+(.+?)\s*$")
_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(```|~~~)")

_SEVERE: Final[frozenset[str]] = frozenset({"critical", "high"})


# ---------------------------------------------------------------------------
# hashing / markdown helpers
# ---------------------------------------------------------------------------


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path | str) -> str:
    """Stream the file: a P4 description plus data exhibits can be large."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split `---` YAML frontmatter from the body. No frontmatter is not an error."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise DossierError(f"unparseable YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise DossierError("frontmatter must be a YAML mapping")
    return data, text[match.end() :]


def render_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    """Serialise frontmatter + body. Key order is preserved (it is read by humans)."""
    yaml_text = yaml.safe_dump(
        _jsonable(frontmatter), sort_keys=False, allow_unicode=True, width=10_000
    ).rstrip()
    return "---\n" + yaml_text + "\n---\n\n" + body.lstrip("\n")


def split_h2_sections(body: str) -> dict[str, str]:
    """Map H2 title -> section text, ignoring headings inside fenced code blocks."""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    fence: str | None = None
    for line in body.splitlines():
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            token = fence_match.group(1)
            if fence is None:
                fence = token
            elif line.strip().startswith(fence):
                fence = None
        if fence is None:
            heading = _H2_RE.match(line)
            if heading:
                if current is not None:
                    sections[current] = "\n".join(buffer).strip()
                current = heading.group(1).strip()
                buffer = []
                continue
        if current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    return sections


def embed_output_text(raw: str, include_rationale: bool = False) -> str:
    """Reduce a stored agent output to what a downstream prompt may see.

    Frontmatter always goes. Unless the consuming prompt explicitly asks for
    rationale, only Deliverable / Assumptions / Open Questions survive - the
    structural half of the blindness guarantee. A file with no `## Deliverable`
    (description.md, claims.md, CONFIRMED.md) is prose, not a contract reply, and
    is embedded whole.
    """
    _, body = split_frontmatter(raw)
    body = _strip_trailing_json_fence(body)
    sections = split_h2_sections(body)
    if include_rationale or "Deliverable" not in sections:
        return body.strip()
    kept = [f"## {name}\n\n{sections[name]}" for name in EMBED_SECTIONS if sections.get(name)]
    return "\n\n".join(kept).strip()


def _strip_trailing_json_fence(body: str) -> str:
    """The envelope is stored beside the output; it must not be embedded twice."""
    match = re.search(r"\n```json\s*\n.*?\n```\s*\Z", body, re.DOTALL)
    return body[: match.start()].rstrip() if match else body


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# ---------------------------------------------------------------------------
# input-spec grammar
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedSpec:
    """A syntactically valid input spec. Says nothing about what exists on disk."""

    family: str
    arg: str = ""
    any_severity: bool = False


def validate_spec(spec: str) -> ParsedSpec:
    """Parse an input spec or raise `UnknownInputSpec`.

    Shared by `resolve_input` and `playbook.validate` so the prompt files and the
    runner can never drift apart on the grammar.
    """
    text = (spec or "").strip()
    if not text:
        raise UnknownInputSpec(spec)
    if text in _BARE_SPECS:
        return ParsedSpec(text)
    head, sep, rest = text.partition(":")
    if not sep:
        raise UnknownInputSpec(spec)
    if head == "intake":
        if not rest:
            raise UnknownInputSpec(spec)
        return ParsedSpec("intake", rest)
    if head == "out":
        phase, slash, prompt_id = rest.partition("/")
        if not slash or phase not in PHASES or not prompt_id:
            raise UnknownInputSpec(spec)
        return ParsedSpec("out", rest)
    if head == "package":
        if rest not in PACKAGE_MEMBERS and rest != "all":
            raise UnknownInputSpec(spec)
        return ParsedSpec("package", rest)
    if head == "findings":
        category, _, modifier = rest.partition(":")
        if category not in FINDING_CATEGORIES and category != "all":
            raise UnknownInputSpec(spec)
        if modifier not in ("", "any"):
            raise UnknownInputSpec(spec)
        return ParsedSpec("findings", category, any_severity=modifier == "any")
    raise UnknownInputSpec(spec)


class ResolvedInput(BaseModel):
    """One embeddable dossier file (or a derived listing/extract)."""

    spec: str
    path: Path | None
    text: str
    sha256: str
    chars: int
    kind: str

    @property
    def label(self) -> str:
        """What the prompt builder prints as the `### <path>` heading."""
        return str(self.path) if self.path is not None else f"<{self.spec}>"

    def ref(self, root: Path | None = None) -> dict[str, str]:
        """Frontmatter `inputs:` entry - what this output was derived from."""
        if self.path is None:
            return {"path": f"<{self.spec}>", "sha256": self.sha256}
        path = self.path
        if root is not None:
            with contextlib.suppress(ValueError):
                path = self.path.relative_to(root)
        return {"path": str(path), "sha256": self.sha256}


@dataclass
class InputContext:
    """Everything `resolve_input` needs beyond the dossier itself."""

    phase: str | None = None
    prompt_id: str | None = None
    include_rationale: bool = False
    # Set while a review package is being assembled, before the review version
    # is complete: reads must come from the in-progress directory.
    package_dir: Path | None = None
    review_version: int | None = None
    # Optional inputs that resolved to nothing, for the run report.
    missing: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VersionDir:
    """One `v<N>-<date>` directory of one phase."""

    phase: str
    v: int
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def version_json(self) -> Path:
        return self.path / "VERSION.json"

    def read_version_json(self) -> dict[str, Any]:
        try:
            return json.loads(self.version_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DossierError(f"unreadable {self.version_json}: {exc}") from exc


# ---------------------------------------------------------------------------
# Dossier
# ---------------------------------------------------------------------------


class Dossier:
    """Filesystem access to one matter. Holds no run state of its own."""

    def __init__(
        self,
        root: Path | str,
        settings: Settings,
        pins: dict[str, int] | None = None,
    ) -> None:
        self.root = Path(root)
        self.settings = settings
        # `--pin P3=2` style override: read an older version deliberately.
        self.pins: dict[str, int] = dict(pins or {})

    # -- layout -------------------------------------------------------------

    @property
    def slug(self) -> str:
        return self.root.name

    @property
    def matter_path(self) -> Path:
        return self.root / "matter.json"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def log_path(self) -> Path:
        return self.root / "log.jsonl"

    @property
    def progress_path(self) -> Path:
        """The run ticker: one line per step, written the moment it happens."""
        return self.root / "progress.md"

    @property
    def intake_dir(self) -> Path:
        return self.root / "00-intake"

    @property
    def terminology_dir(self) -> Path:
        return self.root / "terminology"

    @property
    def numerals_dir(self) -> Path:
        return self.root / "numerals"

    @property
    def gates_dir(self) -> Path:
        return self.root / "gates"

    @property
    def briefs_dir(self) -> Path:
        return self.root / "briefs"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def locked_terminology_path(self) -> Path:
        return self.terminology_dir / "LOCKED.md"

    @property
    def registry_path(self) -> Path:
        return self.numerals_dir / "registry.json"

    @property
    def crosscheck_path(self) -> Path:
        return self.numerals_dir / "cross-check.json"

    @property
    def drift_path(self) -> Path:
        return self.terminology_dir / "drift.jsonl"

    def phase_dir(self, phase: str) -> Path:
        return self.root / phase_dir_name(phase)

    def run_dir(self, call_id: str) -> Path:
        """`runs/<call_id>/`, created on demand: the prompt file is the artefact."""
        path = self.runs_dir / call_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def exists(self) -> bool:
        return self.matter_path.is_file()

    # -- creation -----------------------------------------------------------

    def create(
        self,
        slug: str,
        title: str,
        jurisdictions: list[str],
        from_example: Path | str | None = None,
    ) -> Dossier:
        """Build the tree of plan s3.4. Refuses to touch an existing matter."""
        if slug != self.root.name:
            raise DossierError(f"slug {slug!r} does not match dossier directory {self.root.name!r}")
        if self.matter_path.exists():
            raise WouldOverwrite(f"matter already exists at {self.root}")

        for directory in (
            self.intake_dir,
            self.intake_dir / "prior-art",
            self.intake_dir / "priority",
            self.intake_dir / "data",
            self.intake_dir / "drawings",
            self.terminology_dir,
            self.numerals_dir,
            self.gates_dir,
            self.briefs_dir,
            self.runs_dir,
            *(self.root / name for name in PHASES.values()),
        ):
            directory.mkdir(parents=True, exist_ok=True)

        if from_example is not None:
            self._seed_from_example(Path(from_example))

        matter = {
            "slug": slug,
            "title": title,
            "jurisdictions": list(jurisdictions),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.matter_path.write_text(json.dumps(matter, indent=2) + "\n", encoding="utf-8")
        # An empty matter is still a valid, resumable one: write state.json now so
        # every later command can assume it exists.
        save_state(new_state(slug, title, list(jurisdictions)), self.state_path)
        self.log_path.touch(exist_ok=True)
        return self

    def _seed_from_example(self, example: Path) -> None:
        """Copy a fixture matter's intake so a smoke run has something to chew on."""
        if not example.is_dir():
            raise DossierError(f"example matter not found: {example}")
        source = example / "00-intake" if (example / "00-intake").is_dir() else example
        for item in sorted(source.iterdir()):
            if item.name in {"matter.json", "state.json", "log.jsonl"}:
                continue
            target = self.intake_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)

    # -- versions -----------------------------------------------------------

    def versions(self, phase: str) -> list[VersionDir]:
        """Every `v<N>-<date>` directory of a phase, ascending by N."""
        directory = self.phase_dir(phase)
        found: list[VersionDir] = []
        if not directory.is_dir():
            return found
        for child in directory.iterdir():
            match = _VERSION_DIR_RE.match(child.name)
            if child.is_dir() and match:
                found.append(VersionDir(phase, int(match.group(1)), child))
        found.sort(key=lambda vd: vd.v)
        return found

    def version(self, phase: str, v: int) -> VersionDir | None:
        return next((vd for vd in self.versions(phase) if vd.v == v), None)

    def new_version(self, phase: str, trigger: str, today: str | None = None) -> VersionDir:
        """Open `v<N+1>-<date>`.

        N counts every directory ever created, complete or not, so a failed
        version still burns its number: a reader can tell `v2` failed from `v3`
        existing, and no path is ever reused for different content.
        """
        existing = self.versions(phase)
        next_v = max((vd.v for vd in existing), default=0) + 1
        date = today or datetime.now(UTC).strftime("%Y-%m-%d")
        path = self.phase_dir(phase) / f"v{next_v}-{date}"
        if path.exists():
            raise WouldOverwrite(f"version directory already exists: {path}")
        path.mkdir(parents=True)
        payload = {
            "v": next_v,
            "dir": path.name,
            "phase": phase,
            "trigger": trigger,
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "finished_at": None,
        }
        (path / "VERSION.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return VersionDir(phase, next_v, path)

    def complete_version(self, phase: str, v: int, status: str = "complete") -> VersionDir:
        """Mark a version finished. Only VERSION.json changes; artefacts never do."""
        version_dir = self.version(phase, v)
        if version_dir is None:
            raise DossierError(f"no version v{v} of phase {phase}")
        data = version_dir.read_version_json()
        data["status"] = status
        data["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        version_dir.version_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return version_dir

    def latest(self, phase: str) -> VersionDir | None:
        """Highest complete version, or the pinned one when the caller pinned it."""
        pinned = self.pins.get(phase)
        if pinned is not None:
            version_dir = self.version(phase, pinned)
            if version_dir is None:
                raise DossierError(f"pinned version v{pinned} of phase {phase} does not exist")
            return version_dir
        for version_dir in reversed(self.versions(phase)):
            try:
                if version_dir.read_version_json().get("status") == "complete":
                    return version_dir
            except DossierError as exc:
                log.warning("%s", exc)
        return None

    def complete_versions_desc(self, phase: str) -> list[VersionDir]:
        """Complete versions, newest first. Pins collapse this to the pinned one."""
        pinned = self.latest(phase) if phase in self.pins else None
        if pinned is not None:
            return [pinned]
        out: list[VersionDir] = []
        for version_dir in reversed(self.versions(phase)):
            try:
                if version_dir.read_version_json().get("status") == "complete":
                    out.append(version_dir)
            except DossierError as exc:
                log.warning("%s", exc)
        return out

    def readable_versions_desc(self, phase: str, include_running: bool = False) -> list[VersionDir]:
        """Versions whose artefacts may be read, newest first.

        A *running* version is visible only to prompts of its own phase
        (`include_running`). Inside one phase entry, `status` describes the entry
        and not the files: an output is written once and never rewritten, so
        Prompt 02 may read Prompt 01's output without waiting for Phase 1 to
        finish. From outside the phase, an unfinished version does not exist -
        a later phase reading half a Phase 3 would draft from material the gate
        has not seen.
        """
        if not include_running:
            return self.complete_versions_desc(phase)
        pinned = self.latest(phase) if phase in self.pins else None
        if pinned is not None:
            return [pinned]
        out: list[VersionDir] = []
        for version_dir in reversed(self.versions(phase)):
            try:
                if version_dir.read_version_json().get("status") in {"complete", "running"}:
                    out.append(version_dir)
            except DossierError as exc:
                log.warning("%s", exc)
        return out

    # -- outputs ------------------------------------------------------------

    def latest_output(
        self, phase: str, prompt_id: str, include_running: bool = False
    ) -> Path | None:
        """Newest `<id>-*.md` (or `<id>.md`) for a prompt.

        Searches readable versions newest-first rather than only the latest: a
        revise-only version legitimately contains just the prompts it re-ran, and
        the untouched analysis from the previous version is still current.
        """
        for version_dir in self.readable_versions_desc(phase, include_running):
            found = _find_output(version_dir.path, prompt_id)
            if found is not None:
                return found
        return None

    def write_output(
        self,
        phase: str,
        v: int,
        prompt_id: str,
        title_slug: str,
        body_md: str,
        frontmatter: dict[str, Any],
        envelope: dict[str, Any] | None = None,
    ) -> Path:
        """Write `<id>-<title_slug>.md` (+ `<id>.envelope.json`). Never overwrites."""
        version_dir = self.version(phase, v)
        if version_dir is None:
            raise DossierError(f"no version v{v} of phase {phase}; call new_version() first")

        path = version_dir.path / f"{prompt_id}-{title_slug}.md"
        if path.exists():
            raise WouldOverwrite(f"{path} already exists; versions are immutable")

        head: dict[str, Any] = {
            "matter": self.slug,
            "phase": phase,
            "prompt": prompt_id,
            "version": v,
            "status": "candidate",
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        head.update(frontmatter)
        path.write_text(render_frontmatter(head, body_md), encoding="utf-8")

        if envelope is not None:
            envelope_path = version_dir.path / f"{prompt_id}.envelope.json"
            if envelope_path.exists():
                raise WouldOverwrite(f"{envelope_path} already exists")
            envelope_path.write_text(
                json.dumps(_jsonable(envelope), indent=2) + "\n", encoding="utf-8"
            )
        return path

    def read_output(self, path: Path | str) -> tuple[dict[str, Any], str]:
        text = Path(path).read_text(encoding="utf-8")
        return split_frontmatter(text)

    def mark_status(self, path: Path | str, status: str) -> Path:
        """Flip frontmatter `status` in place - the one permitted edit (gate decisions)."""
        target = Path(path)
        frontmatter, body = self.read_output(target)
        if not frontmatter:
            raise DossierError(f"{target} has no frontmatter to update")
        frontmatter["status"] = status
        target.write_text(render_frontmatter(frontmatter, body), encoding="utf-8")
        return target

    def write_file(self, path: Path, text: str, overwrite: bool = False) -> Path:
        """Write a non-versioned artefact (CONFIRMED.md, registry.json, briefs)."""
        if path.exists() and not overwrite:
            raise WouldOverwrite(f"{path} already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def intake_files(self, kind: str) -> list[Path]:
        """Sorted files the human put under `00-intake/<kind>/`."""
        directory = self.intake_dir / kind
        if not directory.is_dir():
            return []
        return sorted(p for p in directory.iterdir() if p.is_file() and not p.name.startswith("."))

    # -- input resolution ---------------------------------------------------

    def resolve_input(
        self,
        spec: str,
        ctx: InputContext | None = None,
        required: bool = True,
    ) -> list[ResolvedInput]:
        """Resolve one input spec to zero or more embeddable inputs.

        Requiredness is the caller's (the prompt file's `inputs` vs
        `optional_inputs`): missing + required raises `MissingInput`, missing +
        optional records the spec in `ctx.missing` and returns nothing, so the
        run report can say what the agent was not given.
        """
        context = ctx or InputContext()
        parsed = validate_spec(spec)
        handler = getattr(self, f"_resolve_{parsed.family}")
        try:
            return handler(spec, parsed, context)  # type: ignore[no-any-return]
        except MissingInput as exc:
            if required:
                raise
            context.missing.append(spec)
            log.info("optional input %s skipped: %s", spec, exc.detail)
            return []

    def resolve_inputs(
        self,
        specs: list[str],
        ctx: InputContext | None = None,
        required: bool = True,
    ) -> list[ResolvedInput]:
        out: list[ResolvedInput] = []
        for spec in specs:
            out.extend(self.resolve_input(spec, ctx, required=required))
        return out

    # -- resolvers ----------------------------------------------------------

    def _resolve_intake(
        self, spec: str, parsed: ParsedSpec, ctx: InputContext
    ) -> list[ResolvedInput]:
        rest = parsed.arg
        if rest == "drawings":
            return [self._drawings_listing(spec)]
        if any(ch in rest for ch in "*?["):
            matches = sorted(
                p
                for p in self.intake_dir.glob(rest)
                if p.is_file()
                and not p.name.startswith(".")
                # Search provenance (SEARCH-LOG.json, SEARCH-README.md) is for
                # the evidence pack, not for the agents' prompts.
                and not p.name.startswith("SEARCH-")
                and p.suffix.lower() in {".md", ".txt", ""}
            )
            if not matches:
                raise MissingInput(spec, f"no files match 00-intake/{rest}")
            return [self._from_file(spec, p, "intake") for p in matches]

        candidate = self.intake_dir / rest
        if not candidate.is_file():
            candidate = self.intake_dir / f"{rest}.md"
        if not candidate.is_file():
            raise MissingInput(spec, f"no file at {candidate}")
        return [self._from_file(spec, candidate, "intake")]

    def _drawings_listing(self, spec: str) -> ResolvedInput:
        """Names only. Drawings are binary; embedding them would blow the budget."""
        files = self.intake_files("drawings")
        if not files:
            raise MissingInput(spec, "no files under 00-intake/drawings")
        lines = [f"- {p.name} ({p.stat().st_size} bytes)" for p in files]
        text = "Files supplied under 00-intake/drawings (names only, not embedded):\n" + "\n".join(
            lines
        )
        return _derived(spec, text, "listing", self.intake_dir / "drawings")

    def _resolve_out(self, spec: str, parsed: ParsedSpec, ctx: InputContext) -> list[ResolvedInput]:
        phase, _, prompt_id = parsed.arg.partition("/")
        # A prompt may read its own phase's in-progress version; nothing else may.
        path = self.latest_output(phase, prompt_id, include_running=ctx.phase == phase)
        if path is None:
            raise MissingInput(spec, f"no {prompt_id}* output in a complete version of {phase}")
        raw = path.read_text(encoding="utf-8")
        text = embed_output_text(raw, ctx.include_rationale)
        return [
            ResolvedInput(
                spec=spec,
                path=path,
                text=text,
                sha256=sha256_file(path),
                chars=len(text),
                kind="out",
            )
        ]

    def _resolve_confirmed_embodiments(
        self, spec: str, parsed: ParsedSpec, ctx: InputContext
    ) -> list[ResolvedInput]:
        version_dir = self.latest("P3")
        if version_dir is None:
            raise MissingInput(spec, "no complete P3 version")
        path = version_dir.path / "CONFIRMED.md"
        if not path.is_file():
            raise MissingInput(spec, f"{path} does not exist (Gate 2 not decided?)")
        return [self._from_file(spec, path, "confirmed_embodiments")]

    def _resolve_terminology(
        self, spec: str, parsed: ParsedSpec, ctx: InputContext
    ) -> list[ResolvedInput]:
        """LOCKED.md, or the unlocked Prompt-4 table while P1 is still running.

        Before Gate 1 there is no lock, and P1's own prompts must still run. Any
        later phase asking for terminology without a lock is a precondition
        failure, not something to paper over.
        """
        path = self.locked_terminology_path
        if path.is_file():
            return [self._from_file(spec, path, "terminology")]
        if ctx.phase != "P1":
            raise MissingInput(spec, f"{path} does not exist (Gate 1 not decided?)")
        fallback = self.latest_output("P1", "04")
        if fallback is None:
            return [_derived(spec, "", "terminology", None)]
        raw = fallback.read_text(encoding="utf-8")
        text = embed_output_text(raw, ctx.include_rationale)
        return [
            ResolvedInput(
                spec=spec,
                path=fallback,
                text=text,
                sha256=sha256_file(fallback),
                chars=len(text),
                kind="terminology",
            )
        ]

    def _resolve_registry(
        self, spec: str, parsed: ParsedSpec, ctx: InputContext
    ) -> list[ResolvedInput]:
        if not self.registry_path.is_file():
            raise MissingInput(spec, f"{self.registry_path} does not exist (Prompt 20a not run?)")
        return [self._from_file(spec, self.registry_path, "registry")]

    def _resolve_numeral_crosscheck(
        self, spec: str, parsed: ParsedSpec, ctx: InputContext
    ) -> list[ResolvedInput]:
        if not self.crosscheck_path.is_file():
            raise MissingInput(spec, f"{self.crosscheck_path} does not exist")
        return [self._from_file(spec, self.crosscheck_path, "numeral_crosscheck")]

    def _resolve_package(
        self, spec: str, parsed: ParsedSpec, ctx: InputContext
    ) -> list[ResolvedInput]:
        directory = ctx.package_dir or self._review_package_dir()
        if directory is None or not directory.is_dir():
            raise MissingInput(spec, "no review package directory")
        members = list(PACKAGE_MEMBERS) if parsed.arg == "all" else [parsed.arg]
        out: list[ResolvedInput] = []
        for member in members:
            path = directory / PACKAGE_MEMBERS[member]
            if path.is_file():
                out.append(self._from_file(spec, path, "package"))
            elif parsed.arg != "all":
                raise MissingInput(spec, f"{path} does not exist")
        if not out:
            raise MissingInput(spec, f"package directory {directory} is empty")
        return out

    def _review_package_dir(self) -> Path | None:
        version_dir = self.latest("REVIEW")
        return version_dir.path / "package" if version_dir else None

    def _resolve_findings(
        self, spec: str, parsed: ParsedSpec, ctx: InputContext
    ) -> list[ResolvedInput]:
        path = self._review_artefact("findings.json", ctx)
        if path is None:
            raise MissingInput(spec, "no findings.json in a complete REVIEW version")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DossierError(f"{path} is not valid JSON: {exc}") from exc
        findings = data.get("findings", []) if isinstance(data, dict) else data
        selected = [
            f
            for f in findings
            if isinstance(f, dict)
            and (parsed.arg == "all" or f.get("category") == parsed.arg)
            and (parsed.any_severity or str(f.get("severity", "")).lower() in _SEVERE)
        ]
        text = json.dumps({"findings": selected}, indent=2)
        return [
            ResolvedInput(
                spec=spec,
                path=path,
                text=text,
                sha256=sha256_text(text),
                chars=len(text),
                kind="findings",
            )
        ]

    def _resolve_corrections(
        self, spec: str, parsed: ParsedSpec, ctx: InputContext
    ) -> list[ResolvedInput]:
        return [self._envelope_extract(spec, "REVIEW", "corrections", ctx)]

    def _resolve_disclosure_requests(
        self, spec: str, parsed: ParsedSpec, ctx: InputContext
    ) -> list[ResolvedInput]:
        return [self._envelope_extract(spec, "P5", "disclosure_requests", ctx)]

    def _resolve_open_disagreements(
        self, spec: str, parsed: ParsedSpec, ctx: InputContext
    ) -> list[ResolvedInput]:
        return [self._envelope_extract(spec, "P4", "open_disagreements", ctx)]

    def _resolve_inner_loop_flags(
        self, spec: str, parsed: ParsedSpec, ctx: InputContext
    ) -> list[ResolvedInput]:
        path = self._review_artefact("inner-loop-flags.md", ctx)
        if path is None:
            raise MissingInput(spec, "no inner-loop-flags.md in a complete REVIEW version")
        return [self._from_file(spec, path, "inner_loop_flags")]

    # -- resolver helpers ---------------------------------------------------

    def _review_artefact(self, filename: str, ctx: InputContext) -> Path | None:
        """A REVIEW artefact from the pinned/in-progress version, else the latest."""
        if ctx.review_version is not None:
            version_dir = self.version("REVIEW", ctx.review_version)
            if version_dir is None:
                return None
            candidate = version_dir.path / filename
            return candidate if candidate.is_file() else None
        for version_dir in self.complete_versions_desc("REVIEW"):
            candidate = version_dir.path / filename
            if candidate.is_file():
                return candidate
        return None

    def _envelope_extract(
        self, spec: str, phase: str, key: str, ctx: InputContext
    ) -> ResolvedInput:
        """Aggregate one list field across every envelope of a phase's latest version."""
        version_dir = (
            self.version("REVIEW", ctx.review_version)
            if phase == "REVIEW" and ctx.review_version is not None
            else self.latest(phase)
        )
        if version_dir is None:
            raise MissingInput(spec, f"no complete {phase} version")
        items: list[Any] = []
        for envelope_path in sorted(version_dir.path.glob("*.envelope.json")):
            try:
                data = json.loads(envelope_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                log.warning("skipping unreadable envelope %s: %s", envelope_path, exc)
                continue
            value = data.get(key) if isinstance(data, dict) else None
            if isinstance(value, list):
                for item in value:
                    entry = dict(item) if isinstance(item, dict) else {"item": item}
                    entry.setdefault("source_prompt", envelope_path.name.split(".")[0])
                    items.append(entry)
        text = json.dumps({key: items}, indent=2)
        return ResolvedInput(
            spec=spec,
            path=version_dir.path,
            text=text,
            sha256=sha256_text(text),
            chars=len(text),
            kind=key,
        )

    def _from_file(self, spec: str, path: Path, kind: str) -> ResolvedInput:
        text = path.read_text(encoding="utf-8")
        return ResolvedInput(
            spec=spec,
            path=path,
            text=text,
            sha256=sha256_text(text),
            chars=len(text),
            kind=kind,
        )


def _derived(spec: str, text: str, kind: str, path: Path | None) -> ResolvedInput:
    return ResolvedInput(
        spec=spec, path=path, text=text, sha256=sha256_text(text), chars=len(text), kind=kind
    )


def _find_output(version_path: Path, prompt_id: str) -> Path | None:
    exact = version_path / f"{prompt_id}.md"
    if exact.is_file():
        return exact
    matches = sorted(
        p
        for p in version_path.iterdir()
        if p.is_file() and fnmatch.fnmatch(p.name, f"{prompt_id}-*.md")
    )
    return matches[0] if matches else None
