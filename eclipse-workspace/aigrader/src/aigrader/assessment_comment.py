# src/aigrader/assessment_comment.py
#
# Backward compatible comment renderers with optional Revision Report section.
#
# - If GradeRun contains revision fields (previous attempt, timestamps, metrics),
#   both the plain-text and HTML renderers will include a "Revision Report (Informational)"
#   block just before the instructor note.
# - If those fields are absent, renderers behave exactly as before.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Any

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


def _as_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _as_str(v: Any) -> Optional[str]:
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def _format_elapsed(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    try:
        sec = int(seconds)
    except Exception:
        return None
    if sec < 0:
        return None
    # Prefer hours+minutes; show minutes if < 1h
    mins = sec // 60
    hours = mins // 60
    rem_mins = mins % 60
    if hours <= 0:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    if rem_mins == 0:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{hours} hour{'s' if hours != 1 else ''} {rem_mins} minute{'s' if rem_mins != 1 else ''}"


def _render_revision_report_text(run) -> list[str]:
    """
    Returns lines for a student+instructor-visible "Revision Report (Informational)".
    Only rendered if at least one revision signal is available.
    """
    prev_attempt = _as_int(getattr(run, "previous_submission_attempt", None))
    curr_attempt = _as_int(getattr(run, "submission_attempt", None))
    elapsed_s = _as_int(getattr(run, "time_since_previous_attempt_seconds", None))
    depth = _as_str(getattr(run, "revision_depth", None))

    metrics = getattr(run, "revision_metrics", None)
    sentence_change = _as_float(getattr(metrics, "sentence_change_pct", None)) if metrics else None
    word_overlap = _as_float(getattr(metrics, "word_overlap_pct", None)) if metrics else None
    var_before = _as_float(getattr(metrics, "sentence_length_variance_before", None)) if metrics else None
    var_after = _as_float(getattr(metrics, "sentence_length_variance_after", None)) if metrics else None
    para_before = _as_int(getattr(metrics, "paragraph_count_before", None)) if metrics else None
    para_after = _as_int(getattr(metrics, "paragraph_count_after", None)) if metrics else None

    # Determine if there's anything to show
    has_any = any(
        x is not None
        for x in [prev_attempt, curr_attempt, elapsed_s, depth, sentence_change, word_overlap, var_before, var_after, para_before, para_after]
    )
    if not has_any:
        return []

    lines: list[str] = []
    lines.append("")
    lines.append("Revision Report (Informational)")

    # Attempt context (optional)
    if prev_attempt is not None and curr_attempt is not None:
        lines.append(f"Attempts compared: {prev_attempt} → {curr_attempt}")
    elif curr_attempt is not None:
        lines.append(f"Attempt: {curr_attempt}")

    # Timing
    elapsed_h = _format_elapsed(elapsed_s)
    if elapsed_h:
        lines.append(f"Time since previous attempt: {elapsed_h}")

    # Depth label (if computed)
    if depth:
        # normalize capitalization for display
        lines.append(f"Revision depth: {depth.capitalize()}")

    # What changed between drafts (metrics translated)
    bullets: list[str] = []
    if sentence_change is not None:
        bullets.append(f"Approximately {sentence_change:.0f}% of sentences were revised or rewritten.")
    if word_overlap is not None:
        bullets.append(f"Vocabulary overlap between drafts was about {word_overlap:.0f}%.")
    if var_before is not None and var_after is not None:
        if var_after < var_before:
            bullets.append("Sentence length became more uniform, suggesting smoothing and consolidation of prose.")
        elif var_after > var_before:
            bullets.append("Sentence length became more varied, suggesting more expansion and restructuring of prose.")
    if para_before is not None and para_after is not None:
        if para_before == para_after:
            bullets.append("Paragraph structure remained stable; revisions focused on content within paragraphs.")
        else:
            bullets.append(f"Paragraph structure changed ({para_before} → {para_after}), indicating reorganization.")

    if bullets:
        lines.append("")
        lines.append("What changed between drafts:")
        for b in bullets[:5]:
            lines.append(f"- {b}")

    lines.append("")
    lines.append(
        "Note: This report describes observable patterns between draft versions. "
        "It cannot determine whether changes were made independently, through peer feedback, tutoring, or writing tools."
    )
    return lines


def _render_revision_report_html(run) -> str:
    """
    HTML block for the same report. Returns "" if nothing to show.
    """
    lines = _render_revision_report_text(run)
    if not lines:
        return ""
    # Convert the plain-text block to simple HTML. Preserve headings and bullet points.
    # We intentionally keep this very Canvas-safe (p, br, b, ul, li, em).
    heading = "Revision Report (Informational)"
    bullets: list[str] = []
    body_lines: list[str] = []

    in_bullets = False
    for line in lines:
        if line.strip() == heading:
            continue
        if line.strip() == "What changed between drafts:":
            in_bullets = True
            continue
        if line.startswith("- "):
            bullets.append(line[2:].strip())
            continue
        # blank line: stop bullet section after it
        if in_bullets and not line.strip():
            in_bullets = False
            continue
        if line.strip():
            body_lines.append(line.strip())

    html: list[str] = []
    html.append("<p><b>Revision Report (Informational)</b><br>")
    # Body lines (attempts, timing, depth)
    for bl in body_lines:
        html.append(f"{_esc(bl)}<br>")
    html.append("</p>")

    if bullets:
        html.append("<p><b>What changed between drafts:</b></p>")
        html.append("<ul>")
        for b in bullets[:5]:
            html.append(f"<li>{_esc(b)}</li>")
        html.append("</ul>")

    # Note (last line in text version starts with "Note:")
    # We locate it from original lines to keep exact wording.
    note_line = ""
    for line in reversed(lines):
        if line.strip().startswith("Note:"):
            note_line = line.strip()
            break
    if note_line:
        html.append(f"<p><em>{_esc(note_line)}</em></p>")

    return "".join(html)


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

    # NEW: Revision Report (Informational) — student + instructor visible
    parts.extend(_render_revision_report_text(run))

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

    # NEW: Revision Report (Informational) — student + instructor visible
    rev_html = _render_revision_report_html(run)
    if rev_html:
        html.append(rev_html)

    html.append(
        "<p><em>Instructor note:</em> This is an AI-generated suggestion only. "
        "Please review before assigning any points/grade.</p>"
    )

    return "".join(html)
