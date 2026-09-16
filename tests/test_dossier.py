from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecosystem.config import Settings
from ecosystem.dossier import (
    Dossier,
    DossierError,
    InputContext,
    MissingInput,
    UnknownInputSpec,
    WouldOverwrite,
    embed_output_text,
    sha256_file,
    split_frontmatter,
    validate_spec,
)
from tests.conftest import DRAFTING_BODY, write_version

# -- creation ---------------------------------------------------------------


def test_create_builds_the_documented_tree(settings: Settings) -> None:
    d = Dossier(settings.dossier_root("acme"), settings).create("acme", "Acme", ["US", "EP"])

    for relative in (
        "matter.json",
        "state.json",
        "log.jsonl",
        "00-intake/prior-art",
        "00-intake/priority",
        "00-intake/data",
        "00-intake/drawings",
        "terminology",
        "numerals",
        "gates",
        "briefs",
        "runs",
        "01-disclosure",
        "06-review",
        "07-package",
    ):
        assert (d.root / relative).exists(), relative

    matter = json.loads(d.matter_path.read_text())
    assert matter["slug"] == "acme"
    assert matter["jurisdictions"] == ["US", "EP"]


def test_create_refuses_an_existing_matter_and_a_mismatched_slug(settings: Settings) -> None:
    root = settings.dossier_root("acme")
    Dossier(root, settings).create("acme", "Acme", [])
    with pytest.raises(WouldOverwrite):
        Dossier(root, settings).create("acme", "Acme", [])
    with pytest.raises(DossierError):
        Dossier(settings.dossier_root("other"), settings).create("wrong", "X", [])


def test_create_from_example_seeds_intake(settings: Settings, tmp_path: Path) -> None:
    example = tmp_path / "example-matter"
    (example / "00-intake" / "prior-art").mkdir(parents=True)
    (example / "00-intake" / "disclosure.md").write_text("the invention")
    (example / "00-intake" / "prior-art" / "us123.md").write_text("ref")
    (example / "matter.json").write_text("{}")

    d = Dossier(settings.dossier_root("acme"), settings).create(
        "acme", "Acme", [], from_example=example
    )

    assert (d.intake_dir / "disclosure.md").read_text() == "the invention"
    assert (d.intake_dir / "prior-art" / "us123.md").is_file()
    assert json.loads(d.matter_path.read_text())["slug"] == "acme"


# -- versions ---------------------------------------------------------------


def test_new_version_increments_and_never_reuses_a_number(dossier: Dossier) -> None:
    v1 = dossier.new_version("P1", "initial")
    dossier.complete_version("P1", v1.v)
    v2 = dossier.new_version("P1", "inner-loop:2")
    v3 = dossier.new_version("P1", "manual-fix")

    assert [v1.v, v2.v, v3.v] == [1, 2, 3]
    assert v1.path.name.startswith("v1-")
    assert json.loads(v2.version_json.read_text())["trigger"] == "inner-loop:2"
    assert json.loads(v2.version_json.read_text())["status"] == "running"

    # v2 failed: its number is burned, the next entry is v4.
    dossier.complete_version("P1", v2.v, status="failed")
    assert dossier.new_version("P1", "initial").v == 4


def test_latest_is_the_highest_complete_version(dossier: Dossier) -> None:
    assert dossier.latest("P1") is None
    write_version(dossier, "P1", {"01": "first"})
    v2 = write_version(dossier, "P1", {"01": "second"})
    running = dossier.new_version("P1", "inner-loop:2")

    latest = dossier.latest("P1")
    assert latest is not None
    assert latest.v == v2
    assert running.v == 3


def test_pinned_version_overrides_latest(dossier: Dossier) -> None:
    write_version(dossier, "P1", {"01": "first"})
    write_version(dossier, "P1", {"01": "second"})

    dossier.pins["P1"] = 1
    latest = dossier.latest("P1")
    assert latest is not None and latest.v == 1
    assert "first" in (dossier.latest_output("P1", "01") or Path()).read_text()

    dossier.pins["P1"] = 9
    with pytest.raises(DossierError):
        dossier.latest("P1")


def test_latest_output_falls_back_to_an_earlier_version(dossier: Dossier) -> None:
    write_version(dossier, "P4", {"16": "blueprint", "17": "enablement"})
    write_version(dossier, "P4", {"17": "revised enablement"}, trigger="inner-loop:2")

    seventeen = dossier.latest_output("P4", "17")
    sixteen = dossier.latest_output("P4", "16")

    assert seventeen is not None and "revised" in seventeen.read_text()
    assert sixteen is not None and "blueprint" in sixteen.read_text()
    assert dossier.latest_output("P4", "18") is None


# -- outputs ----------------------------------------------------------------


def test_write_output_writes_frontmatter_envelope_and_refuses_overwrite(
    dossier: Dossier,
) -> None:
    v = dossier.new_version("P1", "initial").v
    path = dossier.write_output(
        "P1",
        v,
        "04",
        "terminology",
        "## Deliverable\n\nbody",
        {"agent": "ip-disclosure", "model": "claude-opus-5"},
        envelope={"prompt": "04", "status": "complete"},
    )

    assert path.name == "04-terminology.md"
    frontmatter, body = dossier.read_output(path)
    assert frontmatter["matter"] == "demo"
    assert frontmatter["phase"] == "P1"
    assert frontmatter["prompt"] == "04"
    assert frontmatter["version"] == v
    assert frontmatter["status"] == "candidate"
    assert frontmatter["agent"] == "ip-disclosure"
    assert body.startswith("## Deliverable")
    assert json.loads((path.parent / "04.envelope.json").read_text())["status"] == "complete"

    with pytest.raises(WouldOverwrite):
        dossier.write_output("P1", v, "04", "terminology", "other", {})
    with pytest.raises(DossierError):
        dossier.write_output("P1", 99, "04", "x", "b", {})


def test_mark_status_changes_only_the_status(dossier: Dossier) -> None:
    v = dossier.new_version("P3", "initial").v
    path = dossier.write_output("P3", v, "11", "design-arounds", "## Deliverable\n\nbody", {})
    before = path.read_text()

    dossier.mark_status(path, "confirmed")

    frontmatter, body = dossier.read_output(path)
    assert frontmatter["status"] == "confirmed"
    assert body == split_frontmatter(before)[1]
    assert frontmatter["prompt"] == "11"


def test_mark_status_refuses_a_file_without_frontmatter(dossier: Dossier) -> None:
    plain = dossier.root / "plain.md"
    plain.write_text("no frontmatter")
    with pytest.raises(DossierError):
        dossier.mark_status(plain, "confirmed")


def test_embed_output_text_drops_frontmatter_rationale_and_envelope() -> None:
    raw = (
        "---\nprompt: '11'\n---\n\n"
        + DRAFTING_BODY
        + '\n```json\n{"prompt": "11", "status": "complete"}\n```\n'
    )

    embedded = embed_output_text(raw)

    assert "## Deliverable" in embedded
    assert "## Assumptions" in embedded
    assert "## Open Questions" in embedded
    assert "## Rationale" not in embedded
    assert "narrows scope" not in embedded
    assert "prompt" not in embedded.split("## Assumptions")[0].replace("Deliverable", "")
    assert "```json" not in embedded

    with_rationale = embed_output_text(raw, include_rationale=True)
    assert "## Rationale" in with_rationale


def test_embed_output_text_keeps_prose_without_a_deliverable_heading() -> None:
    raw = "---\nprompt: description\n---\n\n# Description\n\nThe apparatus 10 ...\n"
    assert embed_output_text(raw).startswith("# Description")


# -- input-spec grammar -----------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        "intake:disclosure",
        "intake:prior-art/*",
        "intake:priority/*",
        "intake:data/*",
        "intake:drawings",
        "intake:gate-1-answers",
        "out:P1/04",
        "out:P4/20a",
        "out:P4/description",
        "out:P5/claims",
        "confirmed_embodiments",
        "terminology",
        "registry",
        "numeral_crosscheck",
        "package:description",
        "package:all",
        "findings:novelty",
        "findings:all",
        "findings:all:any",
        "corrections",
        "disclosure_requests",
        "open_disagreements",
        "inner_loop_flags",
    ],
)
def test_every_documented_spec_parses(spec: str) -> None:
    assert validate_spec(spec).family


@pytest.mark.parametrize(
    "spec",
    ["", "intake:", "out:P9/04", "out:P1", "package:everything", "findings:style", "nonsense"],
)
def test_bad_specs_are_rejected(spec: str) -> None:
    with pytest.raises(UnknownInputSpec):
        validate_spec(spec)


# -- input resolution -------------------------------------------------------


@pytest.fixture
def populated(dossier: Dossier) -> Dossier:
    """A dossier far enough along that every spec kind resolves."""
    (dossier.intake_dir / "disclosure.md").write_text("The inventor says...")
    (dossier.intake_dir / "gate-1-answers.md").write_text("Answers")
    (dossier.intake_dir / "prior-art" / "us1.md").write_text("US1")
    (dossier.intake_dir / "prior-art" / "us2.md").write_text("US2")
    (dossier.intake_dir / "drawings" / "fig1.png").write_bytes(b"\x89PNG binary")
    dossier.registry_path.write_text('{"entries": []}')
    dossier.crosscheck_path.write_text('{"ok": true}')

    write_version(dossier, "P1", {"04": DRAFTING_BODY})
    write_version(dossier, "P3", {"11": DRAFTING_BODY}, extra_files={"CONFIRMED.md": "confirmed"})
    write_version(
        dossier,
        "P4",
        {"20a": DRAFTING_BODY},
        envelopes={
            "20a": {"prompt": "20a", "open_disagreements": [{"topic": "t", "position": "p"}]}
        },
        extra_files={"description.md": "# Description\n\nThe housing 10 ..."},
    )
    write_version(
        dossier,
        "P5",
        {"25": DRAFTING_BODY},
        envelopes={
            "25": {
                "prompt": "25",
                "disclosure_requests": [{"limitation": "L1", "needed_disclosure": "D1"}],
            }
        },
        extra_files={"claims.md": "1. A widget."},
    )
    review = dossier.new_version("REVIEW", "initial")
    (review.path / "package").mkdir()
    (review.path / "package" / "description.md").write_text("packaged description")
    (review.path / "package" / "claims.md").write_text("packaged claims")
    (review.path / "package" / "registry.json").write_text("{}")
    (review.path / "findings.json").write_text(
        json.dumps(
            {
                "findings": [
                    {"id": "F1", "category": "novelty", "severity": "critical", "title": "a"},
                    {"id": "F2", "category": "novelty", "severity": "low", "title": "b"},
                    {"id": "F3", "category": "support", "severity": "high", "title": "c"},
                ]
            }
        )
    )
    (review.path / "inner-loop-flags.md").write_text("unresolved")
    (review.path / "37.envelope.json").write_text(
        json.dumps(
            {"corrections": [{"target": "claims", "location": "c1", "from": "a", "to": "b"}]}
        )
    )
    dossier.complete_version("REVIEW", review.v)
    return dossier


def test_resolve_intake_specs(populated: Dossier) -> None:
    ctx = InputContext(phase="P1")

    disclosure = populated.resolve_input("intake:disclosure", ctx)
    assert len(disclosure) == 1
    assert disclosure[0].text == "The inventor says..."
    assert disclosure[0].sha256 and disclosure[0].chars == len(disclosure[0].text)

    refs = populated.resolve_input("intake:prior-art/*", ctx)
    assert [r.path.name for r in refs if r.path] == ["us1.md", "us2.md"]

    answers = populated.resolve_input("intake:gate-1-answers", ctx)
    assert answers[0].text == "Answers"

    drawings = populated.resolve_input("intake:drawings", ctx)
    assert drawings[0].kind == "listing"
    assert "fig1.png" in drawings[0].text
    assert "PNG" not in drawings[0].text.replace("fig1.png", "")


def test_resolve_out_specs_embed_deliverable_only(populated: Dossier) -> None:
    ctx = InputContext(phase="P4")

    four = populated.resolve_input("out:P1/04", ctx)[0]
    assert "## Rationale" not in four.text
    assert "## Deliverable" in four.text
    assert four.sha256 == sha256_file(four.path or Path())

    description = populated.resolve_input("out:P4/description", ctx)[0]
    assert description.text.startswith("# Description")

    claims = populated.resolve_input("out:P5/claims", ctx)[0]
    assert claims.text == "1. A widget."

    rationale = populated.resolve_input("out:P1/04", InputContext(include_rationale=True))[0]
    assert "## Rationale" in rationale.text


def test_resolve_derived_and_dossier_specs(populated: Dossier) -> None:
    ctx = InputContext(phase="P4")

    assert populated.resolve_input("confirmed_embodiments", ctx)[0].text == "confirmed"
    assert populated.resolve_input("registry", ctx)[0].kind == "registry"
    assert populated.resolve_input("numeral_crosscheck", ctx)[0].kind == "numeral_crosscheck"
    assert populated.resolve_input("inner_loop_flags", ctx)[0].text == "unresolved"

    package = populated.resolve_input("package:all", ctx)
    assert sorted(p.path.name for p in package if p.path) == [
        "claims.md",
        "description.md",
        "registry.json",
    ]
    assert populated.resolve_input("package:description", ctx)[0].text == "packaged description"

    corrections = json.loads(populated.resolve_input("corrections", ctx)[0].text)
    assert corrections["corrections"][0]["from"] == "a"

    requests = json.loads(populated.resolve_input("disclosure_requests", ctx)[0].text)
    assert requests["disclosure_requests"][0]["limitation"] == "L1"

    disagreements = json.loads(populated.resolve_input("open_disagreements", ctx)[0].text)
    assert disagreements["open_disagreements"][0]["topic"] == "t"


def test_findings_filter_by_category_and_severity(populated: Dossier) -> None:
    ctx = InputContext(phase="REVIEW")

    novelty = json.loads(populated.resolve_input("findings:novelty", ctx)[0].text)["findings"]
    assert [f["id"] for f in novelty] == ["F1"]

    everything = json.loads(populated.resolve_input("findings:all", ctx)[0].text)["findings"]
    assert [f["id"] for f in everything] == ["F1", "F3"]

    any_severity = json.loads(populated.resolve_input("findings:all:any", ctx)[0].text)["findings"]
    assert [f["id"] for f in any_severity] == ["F1", "F2", "F3"]


def test_terminology_falls_back_to_prompt_04_during_p1(populated: Dossier) -> None:
    during_p1 = populated.resolve_input("terminology", InputContext(phase="P1"))[0]
    assert "## Deliverable" in during_p1.text

    with pytest.raises(MissingInput):
        populated.resolve_input("terminology", InputContext(phase="P4"))

    populated.locked_terminology_path.write_text("| Preferred |\n|---|\n| widget |")
    locked = populated.resolve_input("terminology", InputContext(phase="P4"))[0]
    assert locked.path == populated.locked_terminology_path


def test_terminology_is_empty_when_p1_has_not_run(dossier: Dossier) -> None:
    resolved = dossier.resolve_input("terminology", InputContext(phase="P1"))
    assert resolved[0].text == ""
    assert resolved[0].path is None


def test_missing_required_raises_missing_optional_is_reported(dossier: Dossier) -> None:
    ctx = InputContext(phase="P1")

    with pytest.raises(MissingInput) as excinfo:
        dossier.resolve_input("intake:disclosure", ctx)
    assert "intake:disclosure" in str(excinfo.value)

    assert dossier.resolve_input("intake:prior-art/*", ctx, required=False) == []
    assert dossier.resolve_input("intake:drawings", ctx, required=False) == []
    assert ctx.missing == ["intake:prior-art/*", "intake:drawings"]

    with pytest.raises(MissingInput):
        dossier.resolve_input("out:P1/04", ctx)
    with pytest.raises(UnknownInputSpec):
        dossier.resolve_input("nope", ctx)


def test_incomplete_version_is_not_visible_to_inputs(dossier: Dossier) -> None:
    version = dossier.new_version("P1", "initial")
    dossier.write_output("P1", version.v, "04", "terminology", DRAFTING_BODY, {})

    with pytest.raises(MissingInput):
        dossier.resolve_input("out:P1/04", InputContext(phase="P2"))

    dossier.complete_version("P1", version.v)
    assert dossier.resolve_input("out:P1/04", InputContext(phase="P2"))


def test_run_dir_and_intake_files(dossier: Dossier) -> None:
    path = dossier.run_dir("demo-p1-01-v1-a0")
    assert path.is_dir() and path.parent.name == "runs"
    assert dossier.intake_files("prior-art") == []
    (dossier.intake_dir / "prior-art" / "a.md").write_text("x")
    (dossier.intake_dir / "prior-art" / ".hidden").write_text("x")
    assert [p.name for p in dossier.intake_files("prior-art")] == ["a.md"]


def test_package_reads_an_in_progress_directory_when_given_one(
    populated: Dossier, tmp_path: Path
) -> None:
    in_progress = tmp_path / "package"
    in_progress.mkdir()
    (in_progress / "description.md").write_text("draft description")

    ctx = InputContext(phase="REVIEW", package_dir=in_progress)
    resolved = populated.resolve_input("package:description", ctx)[0]

    assert resolved.text == "draft description"
    assert populated.resolve_input("package:abstract", ctx, required=False) == []
    assert ctx.missing == ["package:abstract"]


def test_review_artefacts_can_be_pinned_to_a_specific_version(populated: Dossier) -> None:
    running = populated.new_version("REVIEW", "loopback:novelty:1")
    (running.path / "findings.json").write_text('{"findings": []}')

    pinned = populated.resolve_input(
        "findings:all", InputContext(phase="REVIEW", review_version=running.v)
    )[0]
    latest = populated.resolve_input("findings:all", InputContext(phase="REVIEW"))[0]

    assert json.loads(pinned.text)["findings"] == []
    assert len(json.loads(latest.text)["findings"]) == 2
