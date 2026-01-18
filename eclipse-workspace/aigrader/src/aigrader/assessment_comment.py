# aigrader/assessment_comment.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from aigrader.grader import GradeRun
from aigrader.score_parser import AssessmentResult


@dataclass(frozen=True)
class CommentMetadata:
    # Optional, but nice for traceability
    model: Optional[str] = None
    response_id: Optional[str] = None


def _now_et_string() -> str:
    # Your instance timezone: America/New_York
    ts = datetime.now(tz=ZoneInfo("America/New_York"))
    # Example: 2026-01-18 11:23 AM ET
    return ts.strftime("%Y-%m-%d %I:%M %p ET")


def render_ai_assessment_comment(
    run: GradeRun,
    assessment: AssessmentResult,
    meta: Optional[CommentMetadata] = None,
) -> str:
    """
    Return a plain-text comment suitable for Canvas submission comments.
    Canvas comments preserve newlines but not arbitrary whitespace, so keep it
    line-oriented with short labels and blank lines between sections.
    """

    lines: list[str] = []

    lines.append("AI Assessment (Not Applied)")
    lines.append(f"Generated: {_now_et_string()}")

    if meta and (meta.model or meta.response_id):
        parts = []
        if meta.model:
            parts.append(f"model={meta.model}")
        if meta.response_id:
            parts.append(f"response_id={meta.response_id}")
        lines.append("Trace: " + " | ".join(parts))

    lines.append("")
    lines.append(f"Assignment: {run.preflight.assignment_name} (id={run.preflight.assignment_id})")
    lines.append(f"Rubric: {run.rubric.title} (Total {run.rubric.points_total:g} pts)")
    lines.append(f"Submission: user_id={run.preflight.submission_user_id} ({run.preflight.submission_word_count} words)")
    lines.append("")
    lines.append(f"Suggested Overall Score: {assessment.overall_score:g} / {run.rubric.points_total:g}")
    lines.append("Suggested Overall Comment:")
    lines.append(assessment.overall_comment.strip())

    lines.append("")
    lines.append("Suggested Rubric Breakdown (Not Applied):")

    # Print in rubric order
    for crit in run.rubric.criteria:
        cid = crit.id
        a = assessment.criteria.get(cid)
        if a is None:
            # Should not happen if parser validated; but keep safe.
            lines.append(f"- {crit.description} ({crit.points:g} pts): [missing AI result]")
            continue

        lines.append("")
        lines.append(f"- {crit.description} ({crit.points:g} pts)")
        lines.append(f"  Suggested: {a.score:g} / {crit.points:g}")
        lines.append("  Rationale:")
        lines.append(a.comment.strip())

    lines.append("")
    lines.append("Instructor note: This is an AI-generated suggestion only. Please review before assigning any points/grade.")
    return "\n".join(lines)
