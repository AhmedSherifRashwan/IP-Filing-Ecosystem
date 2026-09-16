"""Generate contract-valid agent replies for the replay tests.

Sixty hand-written markdown replies would be sixty chances to get the output
contract subtly wrong, and every one of them would have to be re-edited when the
contract changes. Instead this module builds a reply from the prompt's own
frontmatter - its kind and its `deliverable_sections` - so a fixture is valid by
construction and a contract change shows up as a test failure, not as sixty
stale files.

The text is fictional but *shaped* like the real thing: locked terminology used
verbatim, reference numerals that parse, a claim set that looks like a claim
set. That matters, because the runner's deterministic checks (terminology,
numeral registry, section extraction) run against these replies for real.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ecosystem.calls import call_ids
from ecosystem.playbook import Playbook, PromptSpec
from tests.fakes import make_payload

__all__ = [
    "CLAIMS",
    "DESCRIPTION",
    "REGISTRY_TABLE",
    "TERMINOLOGY_TABLE",
    "Scenario",
    "drafting_reply",
    "fixture_key",
    "materialise",
    "prose_reply",
    "reply_for",
    "review_reply",
]

# --------------------------------------------------------------------------
# Fictional matter content. Every preferred term below appears verbatim in the
# generated Deliverables, so `terminology.check_output` passes; no term from an
# avoid column ever appears.
# --------------------------------------------------------------------------

TERMINOLOGY_TABLE = """| Preferred term | Synonyms | Broader (genus) | Narrower (species) | \
Functional description | Terms to avoid | Needs definition |
| --- | --- | --- | --- | --- | --- | --- |
| seat ring | seat insert | annular seat member | superelastic seat ring | \
flexes radially outward to open the purge gap | rubber grommet | yes |
| purge gap | purge clearance | annular clearance | radial purge gap | \
admits pump air across the surfaces where sealant accumulates | escape hole | no |
| poppet | closure member | movable valve member | conical poppet | \
seals against the seat ring and carries the purge shoulder | needle | no |
| over-travel | extra stroke | axial displacement | threshold over-travel | \
poppet movement beyond the normal opening stroke | blow-by | no |"""

REGISTRY_TABLE = """| Numeral | Component | First figure | Also appears in |
| --- | --- | --- | --- |
| 10 | valve stem | 1 | 2, 3 |
| 20 | core body | 1 | 2, 3 |
| 30 | seat ring | 2 | 3 |
| 40 | poppet | 2 | 3 |
| 50 | return spring | 2 | - |
| 60 | purge gap | 3 | - |"""

DESCRIPTION = """[0001] This disclosure relates to a valve core for a \
sealant-carrying tyre assembly.

[0002] Referring to Figure 1, a valve stem 10 receives a core body 20. The core \
body 20 carries a seat ring 30 and guides a poppet 40 under a return spring 50.

[0003] The seat ring 30 is formed of a superelastic alloy and has a convoluted \
wall, so that an axial load applied to its upper edge produces radial expansion.

[0004] In use, the poppet 40 lifts from the seat ring 30 during ordinary \
inflation. Above a threshold pressure differential the poppet 40 undergoes \
over-travel, driving a purge shoulder against the seat ring 30 and opening a \
purge gap 60 between the seat ring 30 and the bore of the core body 20.

[0005] Air at pump pressure passes through the purge gap 60 across the surfaces \
on which sealant would otherwise accumulate. On withdrawal of the pump head the \
seat ring 30 recovers, wiping the face of the poppet 40.

[0006] In a preferred embodiment the seat ring 30 has a wall thickness of 0.20 \
mm, and useful results are obtained between 0.15 mm and 0.30 mm."""

CLAIMS = """1. A valve core comprising: a core body 20; a seat ring 30 carried \
by the core body 20 and arranged to expand radially under axial load; and a \
poppet 40 movable relative to the seat ring 30 between a closed position, an \
open position, and an over-travel position in which the poppet 40 loads the seat \
ring 30 so as to open a purge gap 60 between the seat ring 30 and the core body 20.

2. The valve core of claim 1, wherein the seat ring 30 comprises a superelastic \
alloy.

3. The valve core of claim 1, wherein the seat ring 30 has a convoluted wall.

4. The valve core of claim 1, further comprising a return spring 50 having a \
first rate over the open position and a second, higher rate over the over-travel \
position.

5. A method of purging a valve core, the method comprising: applying a pressure \
differential to a poppet 40 sufficient to move the poppet 40 into an over-travel \
position; opening a purge gap 60 between a seat ring 30 and a core body 20; and \
passing gas through the purge gap 60."""

ABSTRACT = """A valve core has a core body, a seat ring of superelastic alloy \
and a poppet. Above a threshold pressure differential the poppet reaches an \
over-travel position, loads the seat ring and opens a purge gap between the seat \
ring and the core body, so that gas passes across the surfaces on which sealant \
accumulates and the seat ring wipes the poppet on recovery."""

_GENERIC = """Content for this section of the fictional example matter. The seat \
ring 30 and the purge gap 60 are described with the locked preferred terms; the \
poppet 40 reaches over-travel above the threshold differential.

| item | note |
| --- | --- |
| seat ring 30 | superelastic, convoluted wall |
| purge gap 60 | closed at rest, opens on over-travel |"""

# Deliverable sections whose exact shape the runner parses.
_SPECIAL_SECTIONS: dict[str, str] = {
    "Terminology Table": TERMINOLOGY_TABLE,
    "Reference-Numeral Registry": REGISTRY_TABLE,
    "Description": DESCRIPTION,
    "Final Claim Set": CLAIMS,
    "Abstract": ABSTRACT,
    "Corrected Text": CLAIMS,
}


# --------------------------------------------------------------------------
# reply builders
# --------------------------------------------------------------------------


def drafting_reply(
    prompt_id: str,
    sections: list[str],
    *,
    envelope_extra: dict[str, Any] | None = None,
    section_text: dict[str, str] | None = None,
) -> str:
    """A reply that satisfies `_contract-drafting.md`."""
    overrides = section_text or {}
    blocks = []
    for name in sections or ["Deliverable"]:
        body = overrides.get(name) or _SPECIAL_SECTIONS.get(name) or _GENERIC
        blocks.append(f"### {name}\n\n{body}")
    envelope: dict[str, Any] = {
        "prompt": prompt_id,
        "status": "complete",
        "assumptions": [
            {
                "id": "A1",
                "text": "The bench prototype values are representative.",
                "blocking": False,
            }
        ],
        "open_questions": [
            {"id": "Q1", "text": "Is the alloy specification fixed?", "blocks": ["P4"]}
        ],
        "new_terms": [],
        "support_refs": [{"item": "seat ring", "support": "[0003]"}],
        "disclosure_requests": [],
        "open_disagreements": [],
        "narrowing_suggested": [],
    }
    envelope.update(envelope_extra or {})
    return (
        "## Deliverable\n\n"
        + "\n\n".join(blocks)
        + "\n\n## Rationale\n\nThe broader formulation was kept because narrowing to the "
        "prototype values would concede scope that the confirmed embodiments support.\n\n"
        "## Assumptions\n\n- A1: The bench prototype values are representative.\n\n"
        "## Open Questions\n\n- Q1: Is the alloy specification fixed? (blocks P4)\n\n"
        "```json\n" + json.dumps(envelope, indent=2) + "\n```\n"
    )


def review_reply(
    prompt_id: str,
    sections: list[str],
    *,
    findings: list[dict[str, Any]] | None = None,
    corrections: list[dict[str, Any]] | None = None,
    gate14: list[dict[str, Any]] | None = None,
    section_text: dict[str, str] | None = None,
) -> str:
    """A reply that satisfies `_contract-review.md`."""
    overrides = section_text or {}
    blocks = [
        f"### {name}\n\n{overrides.get(name) or _GENERIC}" for name in sections or ["Findings"]
    ]
    listed = findings or []
    for finding in listed:
        blocks.append(
            f"### {finding['id']} ({finding['severity']}, {finding['category']})\n\n"
            f"Claim language: \"{(finding.get('claim_refs') or ['1'])[0]}\". "
            f"{finding['attack']}\n\nLeast narrowing action: {finding['least_narrowing_action']}"
        )
    correction_text = "None."
    if corrections:
        correction_text = "\n".join(
            f"- {c['target']} / {c['location']}: replace \"{c['from']}\" with \"{c['to']}\" "
            f"({c['reason']})"
            for c in corrections
        )
    envelope = {
        "prompt": prompt_id,
        "status": "complete",
        "findings": listed,
        "corrections": corrections or [],
        "gate14": gate14 or [],
        "assumptions": [],
        "open_questions": [],
    }
    return (
        "## Findings\n\n"
        + "\n\n".join(blocks)
        + f"\n\n## Corrections\n\n{correction_text}\n\n"
        "## Assumptions\n\n- A1: Common general knowledge includes conventional "
        "Presta valve cores.\n\n"
        "## Open Questions\n\n- Q1: Has a prior-art search been commissioned?\n\n"
        "```json\n" + json.dumps(envelope, indent=2) + "\n```\n"
    )


def prose_reply(title: str, body: str | None = None) -> str:
    """Plain markdown for `x-gate-brief` / `x-package-memo` (no envelope)."""
    return body or (
        f"## What was produced\n\n{title}: the phase ran and produced its outputs.\n\n"
        "## What you need to confirm\n\n- Q1: Is the alloy specification fixed?\n\n"
        "## Recommendation\n\nAccept, on the basis that the open questions do not "
        "block the next phase.\n\n"
        "## What happens next\n\nThe next phase runs once this gate is decided.\n\n"
        "No agent in this system may authorise filing; this package is a drafting aid "
        "and requires review and sign-off by qualified patent counsel before any "
        "filing decision.\n"
    )


def reply_for(spec: PromptSpec, **overrides: Any) -> str:
    """Build the right kind of reply for one prompt spec."""
    if spec.kind == "prose":
        return prose_reply(spec.title, overrides.get("body"))
    if spec.kind == "review":
        return review_reply(
            spec.id,
            list(spec.deliverable_sections),
            findings=overrides.get("findings"),
            corrections=overrides.get("corrections"),
            gate14=overrides.get("gate14"),
            section_text=overrides.get("section_text"),
        )
    return drafting_reply(
        spec.id,
        list(spec.deliverable_sections),
        envelope_extra=overrides.get("envelope_extra"),
        section_text=overrides.get("section_text"),
    )


# --------------------------------------------------------------------------
# fixture materialisation
# --------------------------------------------------------------------------


def fixture_key(
    slug: str, phase: str, prompt_id: str, version: int, slot: str | None = None
) -> str:
    """The replay key `FakeTransport` derives from the session key."""
    _, stem, _ = call_ids(slug, phase, prompt_id, version, 0, slot)
    return stem


@dataclass
class Scenario:
    """Per-fixture overrides, keyed by replay key (`demo-review-32-v1`)."""

    slug: str = "demo"
    max_version: int = 6
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Extra (phase, prompt, slot) combinations beyond each prompt's own phase.
    extra_slots: list[tuple[str, str, str]] = field(default_factory=list)

    def at(self, key: str, **overrides: Any) -> Scenario:
        self.overrides[key] = overrides
        return self


def materialise(playbook: Playbook, out_dir: Path, scenario: Scenario) -> dict[str, Path]:
    """Write one replay fixture per plausible call of this matter.

    A superset is generated deliberately: the test asserts on the *calls the
    runner made*, not on the fixtures that happened to exist, and a missing
    fixture would only tell us the version numbering moved.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    combinations: list[tuple[str, PromptSpec, str | None]] = []
    for spec in playbook.prompts.values():
        combinations.append((spec.phase, spec, None))
        if spec.id == "x-formal-fix":
            # Written into the phase that owns the corrected text.
            combinations.append(("P4", spec, "description-1"))
            combinations.append(("P5", spec, "claims-1"))
            combinations.append(("P4", spec, "description-2"))
            combinations.append(("P5", spec, "claims-2"))
        if spec.id in {"37", "38", "40a"}:
            combinations.append((spec.phase, spec, "recheck"))
        if spec.id == "x-gate-brief":
            for gate in ("1", "2", "3"):
                combinations.append((spec.phase, spec, f"gate-{gate}"))
            for category in ("novelty", "enablement", "design-around", "support", "formal"):
                combinations.append((spec.phase, spec, f"gate-loopback-cap-{category}"))

    for phase, spec, slot in combinations:
        for version in range(1, scenario.max_version + 1):
            key = fixture_key(scenario.slug, phase, spec.id, version, slot)
            overrides = scenario.overrides.get(key, {})
            text = reply_for(spec, **overrides)
            path = out_dir / f"{key}.json"
            path.write_text(
                json.dumps(
                    {
                        "call_id": key,
                        "agent_id": spec.agent,
                        "session_key": f"agent:{spec.agent}:{key}",
                        "prompt_sha256": "",
                        "response": make_payload(
                            text,
                            run_id=f"run-{key}",
                            provider="fake",
                            model=f"fake/{spec.agent}",
                        ),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            written[key] = path
    return written


# --------------------------------------------------------------------------
# scenario pieces the flow test uses
# --------------------------------------------------------------------------


def novelty_finding(source_prompt: str = "32", n: int = 1) -> dict[str, Any]:
    return {
        "id": f"F-{source_prompt}-{n}",
        "source_prompt": source_prompt,
        "category": "novelty",
        "severity": "critical",
        "title": "Claim 1 reads on a conventional two-rate core",
        "claim_refs": ["1"],
        "spec_refs": ["[0004]"],
        "attack": (
            "Claim 1 recites an over-travel position without reciting the radial "
            "expansion that distinguishes it; on the description's own admission a "
            "conventional core reaches an over-travel position."
        ),
        "least_narrowing_action": (
            "Add disclosure of the radial expansion mechanism as an alternative "
            "embodiment rather than narrowing claim 1."
        ),
        "requires_new_disclosure": True,
        "suggested_target": "P3",
    }


def claims_correction() -> dict[str, Any]:
    return {
        "target": "claims",
        "location": "claim 4",
        "from": "a return spring 50 having a first rate",
        "to": "the return spring 50 having a first rate",
        "reason": "antecedent basis: the return spring is introduced in claim 1's preamble",
    }


def gate14_answers(all_yes: bool = True) -> list[dict[str, Any]]:
    return [
        {
            "q": q,
            "answer": "yes" if all_yes or q != 7 else "unclear",
            "evidence": f"Checked against the packaged text for question {q}.",
        }
        for q in range(1, 15)
    ]
