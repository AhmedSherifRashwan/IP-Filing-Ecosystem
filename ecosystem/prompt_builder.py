"""Assemble one agent call from a prompt file plus the dossier.

The assembled prompt is the artefact: it is written to `runs/<call_id>/prompt.md`
before anything is sent, so every call can be reproduced, diffed and audited
without the gateway. Assembly is deterministic - same dossier, same prompt file,
same bytes - which is what makes the replay tests meaningful.

Order is fixed (plan s3.7) and load-bearing:

    _header.md -> ## Task -> ## Dossier files -> ## Locked terminology -> ## Output contract

The reviewer's blindness is enforced upstream, in `dossier.embed_output_text`
(Rationale is stripped) and in the review prompts' input lists (`package:*`
only). This module only has to not undo it, which is why it never re-reads a
file it was not handed by `resolve_input`.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from ecosystem.config import Settings
from ecosystem.dossier import Dossier, InputContext, MissingInput, ResolvedInput
from ecosystem.playbook import PromptSpec, load_fragment
from ecosystem.state import MatterState
from ecosystem.transport import PromptTooLarge

log = logging.getLogger("ipfe")

__all__ = [
    "AssembledPrompt",
    "PromptTooLarge",
    "assemble",
    "contract_fragment_name",
    "synthetic_input",
]

# Kinds whose reply carries the drafting contract. `formal-fix` is not a prompt
# kind (x-formal-fix declares `revise`); it is listed so an override cannot
# accidentally send a formal fix out without a contract.
_DRAFTING_KINDS: Final[frozenset[str]] = frozenset(
    {"analysis", "drafting", "compose", "revise", "formal-fix"}
)
_REVIEW_KINDS: Final[frozenset[str]] = frozenset({"review"})

_VERSION_IN_PATH: Final[re.Pattern[str]] = re.compile(r"^v(\d+)-\d{4}-\d{2}-\d{2}$")
_FENCE_RUN: Final[re.Pattern[str]] = re.compile(r"`{3,}")

_TASK_HEADING: Final[str] = "## Task"
_FILES_HEADING: Final[str] = "## Dossier files"
_TERMS_HEADING: Final[str] = "## Locked terminology"
_CONTRACT_HEADING: Final[str] = "## Output contract"

# Heading, fences and the truncation marker that a re-added optional input costs
# on top of its own text.
_MARKER_ALLOWANCE: Final[int] = 300
# Below this, a truncated input is noise rather than evidence; drop it instead.
_MIN_KEEP_CHARS: Final[int] = 1_000

# Serialised-params overhead of `gateway call agent --params` beyond the message
# itself (agentId, keys, timeout). Deliberately generous: the point of the hint
# is to land on the right side of ARG_MAX, not to predict argv to the byte.
_PARAMS_OVERHEAD_BYTES: Final[int] = 512


class AssembledPrompt(BaseModel):
    """One ready-to-send prompt and the accounting that produced it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    inputs: list[ResolvedInput] = Field(default_factory=list)
    missing_optional: list[str] = Field(default_factory=list)
    chars: int = 0
    transport_hint: str = "gateway-call"
    # Optional inputs the budget forced out, and inputs cut short by
    # --allow-truncate. Both are reported to the human: a prompt that silently
    # lost an input would produce a silently worse output.
    dropped_optional: list[str] = Field(default_factory=list)
    truncated: list[str] = Field(default_factory=list)
    # Required specs that could not be resolved. Only ever non-empty under
    # `tolerate_missing` (dry runs); a real call raises instead.
    missing_required: list[str] = Field(default_factory=list)

    def input_refs(self, root: Path | None = None) -> list[dict[str, str]]:
        """Frontmatter `inputs:` - what the output about to be written derives from."""
        return [item.ref(root) for item in self.inputs]

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.text, encoding="utf-8")
        return path


def contract_fragment_name(kind: str) -> str | None:
    """Which output-contract fragment a prompt kind gets, if any."""
    if kind in _REVIEW_KINDS:
        return "_contract-review"
    if kind in _DRAFTING_KINDS:
        return "_contract-drafting"
    return None


def synthetic_input(spec: str, label: str, text: str, kind: str = "synthetic") -> ResolvedInput:
    """A dossier file the runner materialises itself (the gate pending file).

    It is given a real relative path as its label so the agent - and the human
    reading `prompt.md` - can find the same file on disk.
    """
    from ecosystem.dossier import sha256_text

    return ResolvedInput(
        spec=spec,
        path=Path(label),
        text=text,
        sha256=sha256_text(text),
        chars=len(text),
        kind=kind,
    )


def assemble(
    dossier: Dossier,
    state: MatterState,
    spec: PromptSpec,
    ctx: InputContext,
    settings: Settings,
    *,
    extra_inputs: list[ResolvedInput] | None = None,
    allow_truncate: bool = False,
    tolerate_missing: bool = False,
) -> AssembledPrompt:
    """Build the prompt for one call.

    `extra_inputs` are runner-materialised files (the gate pending JSON for
    `x-gate-brief`); they lead the `## Dossier files` section because the gate
    brief's first instruction is to read the gate from the first embedded file.

    `tolerate_missing` is for `--dry-run` only: a dry run deliberately assembles
    prompts for outputs that do not exist yet, and reporting "input 03 not
    produced yet" is more useful than aborting the table on the second row.
    """
    ctx.include_rationale = ctx.include_rationale or spec.include_rationale
    ctx.phase = ctx.phase or spec.phase
    ctx.prompt_id = ctx.prompt_id or spec.id

    lost = lost_steps(state)
    required, missing_required = _resolve(dossier, spec.inputs, ctx, True, tolerate_missing, lost)
    optional, _ = _resolve(dossier, spec.optional_inputs, ctx, False, tolerate_missing)

    leading = list(extra_inputs or [])
    # LOCKED.md gets its own section; embedding it twice wastes budget and
    # invites the agent to treat one copy as authoritative over the other.
    terminology, required = _split_terminology(required)
    terminology_extra, optional = _split_terminology(optional)
    terminology = terminology or terminology_extra
    if terminology is None:
        terminology = _locked_terminology(dossier, spec)

    prompts_dir = settings.prompts_path
    header = load_fragment("_header", prompts_dir)
    contract_name = contract_fragment_name(spec.kind)
    contract = load_fragment(contract_name, prompts_dir) if contract_name else ""

    plan = _Plan(
        header=header.strip(),
        task=spec.body.strip(),
        contract=contract.strip(),
        terminology=terminology,
        leading=leading,
        required=required,
        optional=optional,
    )
    dropped, truncated = _fit(plan, settings, allow_truncate)

    text = plan.render()
    used = plan.used_inputs()
    return AssembledPrompt(
        text=text,
        inputs=used,
        missing_optional=list(ctx.missing),
        chars=len(text),
        transport_hint=transport_hint(text, settings),
        dropped_optional=dropped,
        truncated=truncated,
        missing_required=missing_required,
    )


def transport_hint(text: str, settings: Settings) -> str:
    """Which backend `AutoTransport` will pick for a prompt of this size.

    Advisory only - the transport decides for itself at call time. This exists so
    `--dry-run` can tell the human that a P4 prompt will go via `agent-cli`
    before they spend a live call finding out.
    """
    size = len(json.dumps(text).encode("utf-8")) + _PARAMS_OVERHEAD_BYTES
    return "gateway-call" if size <= settings.gateway_call_max_bytes else "agent-cli"


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


class _Plan:
    """The assembled parts, still separable so the budget can drop one."""

    def __init__(
        self,
        header: str,
        task: str,
        contract: str,
        terminology: ResolvedInput | None,
        leading: list[ResolvedInput],
        required: list[ResolvedInput],
        optional: list[ResolvedInput],
    ) -> None:
        self.header = header
        self.task = task
        self.contract = contract
        self.terminology = terminology
        self.leading = leading
        self.required = required
        self.optional = optional

    def used_inputs(self) -> list[ResolvedInput]:
        out = [*self.leading, *self.required, *self.optional]
        if self.terminology is not None:
            out.append(self.terminology)
        return out

    def files(self) -> list[ResolvedInput]:
        return [*self.leading, *self.required, *self.optional]

    def fixed_chars(self) -> int:
        """Everything except the dossier-file blocks."""
        return len(self.render(files=[]))

    def render(self, files: list[ResolvedInput] | None = None) -> str:
        blocks = self.files() if files is None else files
        parts = [self.header, f"{_TASK_HEADING}\n\n{self.task}"]

        rendered = [_render_input(item) for item in blocks]
        body = "\n\n".join(rendered) if rendered else _NO_FILES
        parts.append(f"{_FILES_HEADING}\n\n{body}")

        if self.terminology is not None:
            table = self.terminology.text.strip()
            parts.append(f"{_TERMS_HEADING}\n\n{_TERMINOLOGY_PREAMBLE}\n\n{table}")
        if self.contract:
            parts.append(f"{_CONTRACT_HEADING}\n\n{self.contract}")
        return "\n\n".join(part.strip() for part in parts if part.strip()) + "\n"


_NO_FILES: Final[str] = (
    "None. Every input this task declares is either optional and absent, or "
    "supplied in the task text above."
)

_TERMINOLOGY_PREAMBLE: Final[str] = (
    "The table below is the locked vocabulary for this matter. Use the preferred "
    "terms verbatim. A term from the avoid column, or a synonym used where the "
    "preferred term does not appear, is a contract violation unless you declare "
    "it in `new_terms` with the term it maps to and a reason."
)


def lost_steps(state: MatterState) -> set[str]:
    """Steps that ran and produced nothing: failed, or skipped after failing.

    Their absence is a fact of the run, not a broken dossier, so a later step
    that reads them is told what happened instead of the run being stopped.
    """
    return {
        f"out:{run.phase}/{run.prompt}"
        for run in state.agent_runs
        if run.status in {"failed", "skipped"}
    }


def _absence_note(spec: str) -> ResolvedInput:
    text = (
        f"This input is missing: the step that produces `{spec}` failed and the run "
        "continued without it. Do not guess at what it would have said. Work from the "
        "material you do have, say in your Open Questions that this input was absent, "
        "and mark anything that depended on it as unverified."
    )
    return ResolvedInput(spec=spec, path=None, text=text, sha256="", chars=len(text), kind="absent")


def _resolve(
    dossier: Dossier,
    specs: list[str],
    ctx: InputContext,
    required: bool,
    tolerate_missing: bool,
    lost: set[str] | None = None,
) -> tuple[list[ResolvedInput], list[str]]:
    resolved: list[ResolvedInput] = []
    missing: list[str] = []
    for spec in specs:
        try:
            resolved.extend(dossier.resolve_input(spec, ctx, required=required))
        except MissingInput:
            if required and spec in (lost or set()):
                log.warning("required input %s is absent: its step failed", spec)
                resolved.append(_absence_note(spec))
                continue
            if required and not tolerate_missing:
                raise
            missing.append(spec)
    return resolved, missing


def _split_terminology(
    inputs: list[ResolvedInput],
) -> tuple[ResolvedInput | None, list[ResolvedInput]]:
    found = next((item for item in inputs if item.kind == "terminology"), None)
    if found is None:
        return None, inputs
    return found, [item for item in inputs if item is not found]


def _locked_terminology(dossier: Dossier, spec: PromptSpec) -> ResolvedInput | None:
    """LOCKED.md for any prompt that did not ask for it.

    Once the lock exists it applies to every agent and every prompt, whether or
    not that prompt's input list remembered to say so. Before the lock, only P1
    may run at all, and it has nothing to be shown.
    """
    path = dossier.locked_terminology_path
    if not path.is_file():
        return None
    from ecosystem.dossier import sha256_text

    text = path.read_text(encoding="utf-8")
    return ResolvedInput(
        spec="terminology",
        path=path,
        text=text,
        sha256=sha256_text(text),
        chars=len(text),
        kind="terminology",
    )


def _render_input(item: ResolvedInput) -> str:
    return f"### {_heading(item)}\n\n{_fenced(item)}"


def _heading(item: ResolvedInput) -> str:
    label = item.label
    if item.path is not None:
        label = _relative_label(item.path)
    version = _version_of(item.path)
    digest = item.sha256[:12]
    return f"{label} (v{version}, sha256:{digest})" if version else f"{label} (sha256:{digest})"


def _relative_label(path: Path) -> str:
    """Path as the human would type it: relative to the dossier root when we can tell.

    The dossier root is the first ancestor holding `matter.json`; synthetic
    inputs are already relative and have no ancestors to walk.
    """
    if not path.is_absolute():
        return str(path)
    for parent in path.parents:
        if (parent / "matter.json").is_file():
            return str(path.relative_to(parent))
    return path.name


def _version_of(path: Path | None) -> str:
    if path is None:
        return ""
    for part in reversed(path.parts):
        match = _VERSION_IN_PATH.match(part)
        if match:
            return match.group(1)
    return ""


def _fenced(item: ResolvedInput) -> str:
    """Fence the content with a run of backticks longer than any inside it."""
    longest = max((len(m.group(0)) for m in _FENCE_RUN.finditer(item.text)), default=0)
    fence = "`" * max(3, longest + 1)
    language = _language(item)
    return f"{fence}{language}\n{item.text.strip()}\n{fence}"


def _language(item: ResolvedInput) -> str:
    if item.kind in {"findings", "corrections", "disclosure_requests", "open_disagreements"}:
        return "json"
    suffix = item.path.suffix.lower() if item.path is not None else ""
    if suffix == ".json":
        return "json"
    if suffix in {".md", ".markdown", ""}:
        return "markdown"
    return "text"


def _fit(plan: _Plan, settings: Settings, allow_truncate: bool) -> tuple[list[str], list[str]]:
    """Bring the prompt inside `max_prompt_chars` (plan s3.7).

    Optional inputs give way first, largest first: dropping the biggest one
    costs the fewest drops. Required inputs are never touched - truncating a
    specification an agent was told to rely on produces a confidently wrong
    output instead of a loud failure.

    `--allow-truncate` changes exactly one thing: the input that is about to be
    dropped is kept instead, cut to the space that remains, with a visible
    `[TRUNCATED at N chars]` marker. Half an exhibit is sometimes better than
    none, but only when a human asked for that trade and can see it in the run
    report.

    The plan's wording is "drop optionals, then truncate". It is applied in this
    order rather than literally, because once every optional input has been
    dropped the only thing left to cut is a required input, which is forbidden.
    Truncation therefore replaces the last drop rather than following it.
    """
    budget = settings.max_prompt_chars
    dropped: list[str] = []
    truncated: list[str] = []

    # Digest cap (config `max_embed_chars`): long analysis outputs from earlier
    # phases are embedded head-first up to the cap. The agent sees where the
    # cut is and what it lost; the full text stays in the dossier.
    cap = int(getattr(settings, "max_embed_chars", 0) or 0)
    if cap > 0:
        for attr in ("required", "optional"):
            items = getattr(plan, attr)
            capped: list[ResolvedInput] = []
            for item in items:
                if item.chars > cap and _cappable(item.spec):
                    capped.append(_digest(item, cap))
                    truncated.append(item.spec)
                    log.warning(
                        "digest cap: embedded %s head-first (%d of %d chars)",
                        item.spec,
                        cap,
                        item.chars,
                    )
                else:
                    capped.append(item)
            setattr(plan, attr, capped)

    while plan.optional and len(plan.render()) > budget:
        largest = max(plan.optional, key=lambda item: item.chars)
        plan.optional = [item for item in plan.optional if item is not largest]
        headroom = budget - len(plan.render()) - _MARKER_ALLOWANCE
        keep = min(settings.max_input_chars, largest.chars, headroom)
        if allow_truncate and keep >= _MIN_KEEP_CHARS:
            plan.optional = [*plan.optional, _truncate(largest, keep)]
            truncated.append(largest.spec)
            log.warning(
                "prompt over budget: truncated optional input %s to %d chars",
                largest.spec,
                keep,
            )
            break
        dropped.append(largest.spec)
        log.warning(
            "prompt over budget: dropped optional input %s (%d chars)",
            largest.spec,
            largest.chars,
        )

    final = len(plan.render())
    if final > budget:
        raise PromptTooLarge(
            f"prompt is {final} chars after dropping {len(dropped)} optional "
            f"input(s), over the {budget} char budget; required inputs are never "
            "truncated - reduce the prompt's input list"
        )
    return dropped, truncated


_NEVER_CAPPED_PREFIXES = ("intake:", "terminology", "package:", "registry", "numeral_crosscheck")
_NEVER_CAPPED_EXACT = {"out:P4/description", "out:P5/claims", "gate1:answers"}


def _cappable(spec: str) -> bool:
    """Phase outputs can be digested; filing text and the disclosure cannot."""
    return not (spec in _NEVER_CAPPED_EXACT or spec.startswith(_NEVER_CAPPED_PREFIXES))


def _digest(item: ResolvedInput, cap: int) -> ResolvedInput:
    marker = (
        f"\n\n[DIGEST CAP: the first {cap:,} of {item.chars:,} characters are embedded above; "
        f"the rest of this file was not shown to you. Do not assume the omitted part is empty.]"
    )
    return item.model_copy(update={"text": item.text[:cap] + marker, "chars": cap})


def _truncate(item: ResolvedInput, keep: int) -> ResolvedInput:
    marker = f"\n\n[TRUNCATED at {keep} chars]"
    return item.model_copy(update={"text": item.text[:keep] + marker, "chars": keep})
