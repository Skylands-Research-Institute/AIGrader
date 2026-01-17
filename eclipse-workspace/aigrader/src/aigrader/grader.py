from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
from typing import Any, Optional

from .exceptions import PreflightError, SubmissionError, RubricError


_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    """
    Very lightweight HTML -> text conversion suitable for Canvas text-entry submissions.
    (Good enough for preflight + prompt-building later.)
    """
    if html is None:
        return ""
    # Convert common block separators to newlines first
    s = html.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = s.replace("</p>", "\n").replace("</div>", "\n").replace("</li>", "\n")
    # Strip tags
    s = _TAG_RE.sub("", s)
    # Unescape entities (&nbsp; etc.)
    s = unescape(s).replace("\xa0", " ")
    # Normalize whitespace
    s = re.sub(r"\r\n?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))

@dataclass(frozen=True)
class SubmissionSnapshot:
    user_id: int
    word_count: int
    text: str

@dataclass(frozen=True)
class PreflightSummary:
    course_id: int
    assignment_id: int
    assignment_name: str
    rubric_title: str
    rubric_criteria_count: int
    rubric_points_total: float
    submission_user_id: int
    submission_word_count: int

@dataclass(frozen=True)
class GradeRun:
    preflight: PreflightSummary
    submission_text: str
    submission_word_count: int
    
class AIGrader:
    """
    Top-level orchestrator for AI-based grading.

    Phase 1 implements: Canvas preflight only.
    """

    def __init__(self, *, canvas_client=None, llm_client=None, config=None):
        self.canvas_client = canvas_client
        self.llm_client = llm_client
        self.config = config
        
    def get_submission_text(self, course_id: int, assignment_id: int, user_id: int) -> SubmissionSnapshot:
        sub = self.canvas_client.get_submission_text_entry(course_id, assignment_id, user_id)
        body_html = (sub or {}).get("body") or ""
        text = html_to_text(str(body_html))
        return SubmissionSnapshot(
            user_id=int(sub.get("user_id")),
            word_count=word_count(text),
            text=text,
        ) 

    def grade_assignment(
        self,
        course_id: int,
        assignment_id: int,
        user_id: Optional[int] = None,
    ) -> GradeRun:
        """
        Phase 1: Preflight checks only (no OpenAI, no writeback).

        Requires an injected canvas_client providing:
          - get_assignment(course_id, assignment_id) -> dict
          - get_rubric_for_assignment(course_id, assignment_id) -> dict
          - get_submission_text_entry(course_id, assignment_id, user_id|None) -> dict
        """
        if self.canvas_client is None:
            raise PreflightError("AIGrader requires a canvas_client (none provided).")

        # 1) Assignment
        assignment = self.canvas_client.get_assignment(course_id, assignment_id)
        if not assignment or "id" not in assignment:
            raise PreflightError(f"Assignment not found: course_id={course_id}, assignment_id={assignment_id}")

        assignment_name = assignment.get("name") or assignment.get("title") or f"Assignment {assignment_id}"

        # 2) Rubric
        rubric = self.canvas_client.get_rubric_for_assignment(course_id, assignment_id)
        if not rubric:
            raise RubricError("No rubric found/attached to this assignment.")
        rubric_title = rubric.get("title") or rubric.get("name") or "Untitled Rubric"

        criteria = rubric.get("criteria") or rubric.get("data")  # some Canvas shapes differ
        if not isinstance(criteria, list) or len(criteria) == 0:
            raise RubricError("Rubric found, but it has no criteria.")

        points_total = 0.0
        for c in criteria:
            try:
                points_total += float(c.get("points", 0))
            except Exception:
                pass

        # 3) Submission (text entry)
        sub = self.canvas_client.get_submission_text_entry(course_id, assignment_id, user_id)
        if not sub:
            raise SubmissionError("No submission found for this assignment (or for the requested user).")

        sub_user_id = sub.get("user_id")
        if sub_user_id is None:
            raise SubmissionError("Submission returned by Canvas is missing user_id.")

        body_html = sub.get("body")
        if body_html is None or str(body_html).strip() == "":
            raise SubmissionError("Submission exists but has no text-entry body (empty or missing).")

        text = html_to_text(str(body_html))
        wc = word_count(text)

        # Optional minimum word count check (if you set it in config later)
        min_wc = getattr(self.config, "min_word_count", None)
        if isinstance(min_wc, int) and wc < min_wc:
            raise SubmissionError(f"Submission text too short ({wc} words < min_word_count {min_wc}).")

        preflight = PreflightSummary(
            course_id=course_id,
            assignment_id=assignment_id,
            assignment_name=assignment_name,
            rubric_title=rubric_title,
            rubric_criteria_count=len(criteria),
            rubric_points_total=points_total,
            submission_user_id=int(sub_user_id),
            submission_word_count=wc,
        )
        
        snapshot = self.get_submission_text(course_id, assignment_id, int(sub_user_id))

        return GradeRun(
            preflight=preflight,
            submission_text=snapshot.text,
            submission_word_count=snapshot.word_count
        )


