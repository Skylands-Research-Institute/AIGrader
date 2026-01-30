from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

try:
    from zoneinfo import ZoneInfo  # py3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


@dataclass(frozen=True)
class CommentMetadata:
    model: Optional[str] = None
    response_id: Optional[str] = None


def _now_et_string() -> str:
    if ZoneInfo is None:
        return datetime.now().strftime("%Y-%m-%d %I:%M %p")
    ts = datetime.now(tz=ZoneInfo("America/New_York"))
    return ts.strftime("%Y-%m-%d %I:%M %p ET")


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _with_br(s: str) -> str:
    lines = (s or "").strip().splitlines()
    return "<br>".join(_esc(line) for line in lines if line is not None)


def _rubric_points_total(run) -> float:
    """
    Your codebase uses RubricSnapshot.points_total.
    Some earlier drafts used total_points. Support both.
    """
    rubric = getattr(run, "rubric", None)
    if rubric is None:
        return 0.0
    if hasattr(rubric, "points_total"):
        return float(getattr(rubric, "points_total"))
    if hasattr(rubric, "total_points"):
        return float(getattr(rubric, "total_points"))
    return 0.0


def _rubric_title(run) -> str:
    rubric = getattr(run, "rubric", None)
    if rubric is None:
        return "Rubric"
    return str(getattr(rubric, "title", "Rubric"))


def _criteria_list(run):
    rubric = getattr(run, "rubric", None)
    if rubric is None:
        return []
    return list(getattr(rubric, "criteria", []) or [])


def render_ai_assessment_comment(run, result, meta: Optional[CommentMetadata] = None) -> str:
    """
    Plain text renderer.
    """
    ts = _now_et_string()
    p = getattr(run, "preflight", None)

    assignment_name = getattr(p, "assignment_name", "")
    assignment_id = getattr(p, "assignment_id", "")
    submission_user_id = getattr(p, "submission_user_id", "")
    submission_word_count = getattr(p, "submission_word_count", "")

    total = _rubric_points_total(run)

    parts: list[str] = []
    parts.append("AI Assessment (Not Applied)")
    parts.append(f"Generated: {ts}")

    if meta and (meta.model or meta.response_id):
        bits: list[str] = []
        if meta.model:
            bits.append(f"model={meta.model}")
        if meta.response_id:
            bits.append(f"response_id={meta.response_id}")
        parts.append("Trace: " + " | ".join(bits))

    parts.append(f"Assignment: {assignment_name} (id={assignment_id})")
    parts.append(f"Rubric: {_rubric_title(run)} (Total {total:g} pts)")
    parts.append(f"Submission: user_id={submission_user_id} ({submission_word_count} words)")
    parts.append("")

    parts.append(f"Suggested Overall Score: {float(getattr(result, 'overall_score', 0.0)):g} / {total:g}")
    parts.append("Suggested Overall Comment:")
    parts.append(str(getattr(result, "overall_comment", "")).strip())
    parts.append("")

    parts.append("Suggested Rubric Breakdown (Not Applied):")
    crits = _criteria_list(run)
    result_criteria = getattr(result, "criteria", {}) or {}

    for c in crits:
        cid = getattr(c, "id", None)
        if cid is None:
            continue
        a = result_criteria.get(cid)
        if a is None:
            continue

        c_desc = str(getattr(c, "description", "Criterion"))
        c_pts = float(getattr(c, "points", 0.0))
        a_score = float(getattr(a, "score", 0.0))
        a_comment = str(getattr(a, "comment", "")).strip()

        parts.append(f"- {c_desc} ({c_pts:g} pts)")
        parts.append(f"  Suggested: {a_score:g} / {c_pts:g}")
        parts.append(f"  Rationale: {a_comment}")
        parts.append("")

    parts.append("Instructor note: This is an AI-generated suggestion only. Please review before assigning any points/grade.")
    return "\n".join(parts).strip()


def render_ai_assessment_comment_html(run, result, meta: Optional[CommentMetadata] = None) -> str:
    """
    HTML renderer for Canvas.
    Uses only tags Canvas tends to keep: p, br, b, em, ul, li.
    """
    ts = _now_et_string()
    p = getattr(run, "preflight", None)

    assignment_name = getattr(p, "assignment_name", "")
    assignment_id = getattr(p, "assignment_id", "")
    submission_user_id = getattr(p, "submission_user_id", "")
    submission_word_count = getattr(p, "submission_word_count", "")

    total = _rubric_points_total(run)

    html: list[str] = []

    # Header
    html.append("<p><b>AI Assessment (Not Applied)</b><br>")
    html.append(f"Generated: {_esc(ts)}<br>")

    if meta and (meta.model or meta.response_id):
        bits: list[str] = []
        if meta.model:
            bits.append(f"model={meta.model}")
        if meta.response_id:
            bits.append(f"response_id={meta.response_id}")
        html.append(f"Trace: {_esc(' | '.join(bits))}<br>")

    html.append(f"Assignment: {_esc(str(assignment_name))} (id={assignment_id})<br>")
    html.append(f"Rubric: {_esc(_rubric_title(run))} (Total {total:g} pts)<br>")
    html.append(f"Submission: user_id={submission_user_id} ({submission_word_count} words)</p>")

    # Overall
    overall_score = float(getattr(result, "overall_score", 0.0))
    overall_comment = str(getattr(result, "overall_comment", "")).strip()

    html.append(
        f"<p><b>Suggested Overall Score:</b> {overall_score:g} / {total:g}<br>"
        f"<b>Suggested Overall Comment:</b><br>{_with_br(overall_comment)}</p>"
    )

    # Rubric breakdown
    html.append("<p><b>Suggested Rubric Breakdown (Not Applied):</b></p>")
    html.append("<ul>")

    crits = _criteria_list(run)
    result_criteria = getattr(result, "criteria", {}) or {}

    for c in crits:
        cid = getattr(c, "id", None)
        if cid is None:
            continue
        a = result_criteria.get(cid)
        if a is None:
            continue

        c_desc = str(getattr(c, "description", "Criterion"))
        c_pts = float(getattr(c, "points", 0.0))
        a_score = float(getattr(a, "score", 0.0))
        a_comment = str(getattr(a, "comment", "")).strip()

        html.append("<li>")
        html.append(f"<b>{_esc(c_desc)} ({c_pts:g} pts)</b><br>")
        html.append(f"Suggested: {a_score:g} / {c_pts:g}<br>")
        html.append(f"<em>Rationale:</em><br>{_with_br(a_comment)}")
        html.append("</li>")

    html.append("</ul>")

    html.append(
        "<p><em>Instructor note:</em> This is an AI-generated suggestion only. "
        "Please review before assigning any points/grade.</p>"
    )

    return "".join(html)
