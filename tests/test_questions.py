"""Ranking Phase 1's open questions, and the template the human answers in."""

from __future__ import annotations

from typing import Any

from ecosystem.dossier import Dossier
from ecosystem.questions import ANSWERS_TEMPLATE_NAME, top_questions, write_answers_template
from tests.conftest import DRAFTING_BODY, write_version


def _envelope(
    prompt: str,
    questions: list[dict[str, Any]],
    assumptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "status": "complete",
        "open_questions": questions,
        "assumptions": assumptions or [],
    }


def _phase_one(dossier: Dossier, envelopes: dict[str, dict[str, Any]]) -> int:
    outputs = dict.fromkeys(envelopes, DRAFTING_BODY)
    return write_version(dossier, "P1", outputs, envelopes=envelopes)


def test_questions_are_ranked_by_what_they_block(dossier: Dossier) -> None:
    _phase_one(
        dossier,
        {
            "01": _envelope(
                "01",
                [
                    {"id": "Q1", "text": "Which supplier ships the alloy?", "blocks": ["P2"]},
                    {
                        "id": "Q2",
                        "text": "Is the wall thickness essential to the effect?",
                        "blocks": ["P4", "P5"],
                    },
                ],
            ),
            "02": _envelope(
                "02",
                [{"id": "Q3", "text": "Has a search been commissioned?", "blocks": ["P3"]}],
            ),
        },
    )

    ranked = top_questions(dossier)
    assert [question.id for question in ranked] == ["Q2", "Q1", "Q3"]
    assert [question.rank for question in ranked] == [1, 2, 3]
    # P4 + P5 at two points each, plus the "essential" scope keyword.
    assert ranked[0].score == 5
    assert "blocks P4, P5" in ranked[0].why
    assert "essential" in ranked[0].why


def test_a_blocking_assumption_lifts_the_question_it_depends_on(dossier: Dossier) -> None:
    _phase_one(
        dossier,
        {
            "01": _envelope(
                "01",
                [
                    {"id": "Q1", "text": "What is the operating temperature range?", "blocks": []},
                    {"id": "Q2", "text": "Who built the prototype?", "blocks": ["P2"]},
                ],
                assumptions=[
                    {
                        "id": "A1",
                        "text": "The operating temperature range is 0 to 60 degrees.",
                        "blocking": True,
                    }
                ],
            )
        },
    )

    ranked = top_questions(dossier)
    assert ranked[0].id == "Q1"
    assert "blocking assumption A1" in ranked[0].why
    # Two points for the assumption plus one for the "range" scope keyword.
    assert ranked[0].score == 3


def test_the_same_question_from_two_prompts_is_one_entry(dossier: Dossier) -> None:
    _phase_one(
        dossier,
        {
            "01": _envelope(
                "01", [{"id": "Q1", "text": "Is the alloy specification fixed?", "blocks": ["P4"]}]
            ),
            "03": _envelope(
                "03",
                [{"id": "Q7", "text": "is the ALLOY specification fixed?", "blocks": ["P5", "P3"]}],
            ),
        },
    )

    ranked = top_questions(dossier)
    assert len(ranked) == 1
    question = ranked[0]
    assert question.id == "Q1"
    assert question.blocks == ["P4", "P5", "P3"]
    assert question.sources == ["01", "03"]
    assert "raised by 01, 03" in question.why


def test_the_consolidated_prompt_05_list_is_read_too(dossier: Dossier) -> None:
    body = (
        "## Deliverable\n\n"
        "- **Q9**: Is a narrower genus of alloys sufficient for P4?\n"
        "- a bullet that is not a question\n\n"
        "## Rationale\n\nx\n\n## Assumptions\n\n- none\n\n## Open Questions\n\n- none\n"
    )
    version = dossier.new_version("P1", "initial")
    dossier.write_output(
        "P1",
        version.v,
        "05",
        "consolidated",
        body,
        {"agent": "ip-disclosure"},
        envelope=_envelope("05", []),
    )
    dossier.complete_version("P1", version.v)

    ranked = top_questions(dossier)
    assert [question.id for question in ranked] == ["Q9"]
    assert ranked[0].blocks == ["P4"]
    assert ranked[0].sources == ["05-deliverable"]


def test_top_limits_the_list_but_keeps_the_ranking(dossier: Dossier) -> None:
    _phase_one(
        dossier,
        {
            "01": _envelope(
                "01",
                [
                    {"id": f"Q{index}", "text": f"Question number {index}?", "blocks": ["P2"]}
                    for index in range(1, 8)
                ],
            )
        },
    )
    ranked = top_questions(dossier, n=3)
    assert [question.id for question in ranked] == ["Q1", "Q2", "Q3"]


def test_no_phase_one_yields_no_questions(dossier: Dossier) -> None:
    assert top_questions(dossier) == []


def test_the_answers_template_is_numbered_and_never_overwrites_real_answers(
    dossier: Dossier,
) -> None:
    _phase_one(
        dossier,
        {
            "01": _envelope(
                "01",
                [
                    {"id": "Q1", "text": "Is the range essential?", "blocks": ["P4"]},
                    {"id": "Q2", "text": "Which supplier?", "blocks": []},
                ],
            )
        },
    )
    answers = dossier.intake_dir / "gate-1-answers.md"
    answers.write_text("# Gate 1 answers\n\nreal answers the human wrote\n", encoding="utf-8")

    path = write_answers_template(dossier, top_questions(dossier))

    assert path.name == ANSWERS_TEMPLATE_NAME
    text = path.read_text(encoding="utf-8")
    assert "## 1. Q1 (blocks P4)" in text
    assert "## 2. Q2" in text
    assert text.count("**Answer:**") == 2
    assert "--answers-file" in text
    # The template is a separate file; the answers file is untouched.
    assert "real answers" in answers.read_text(encoding="utf-8")


def test_the_template_is_rewritten_in_place_on_a_second_run(dossier: Dossier) -> None:
    _phase_one(
        dossier,
        {"01": _envelope("01", [{"id": "Q1", "text": "Is the range essential?", "blocks": []}])},
    )
    first = write_answers_template(dossier, top_questions(dossier))
    second = write_answers_template(dossier, top_questions(dossier))
    assert first == second
    assert second.read_text(encoding="utf-8").count("## 1. Q1") == 1


def test_an_empty_template_says_so(dossier: Dossier) -> None:
    path = write_answers_template(dossier, [])
    assert "no open questions" in path.read_text(encoding="utf-8")
