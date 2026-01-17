# src/aigrader/grader.py
#
# Regenerated full file:
# - Normalizes rubric long_description via html_to_text
# - Returns GradeRun with clean rubric guidance text
#
# Phase 2.6: Preflight + rubric snapshot (normalized) + submission snapshot.
# No OpenAI, no Canvas writeback.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .canvas import CanvasClient
from .exceptions import AssignmentError, RubricError, SubmissionError
from .textutil import html_to_text, word_count


# -----------------------------
# Result data structures
# -----------------------------

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
class RubricCriterion:
    id: str
    description: str
    long_description: str
    points: float


@dataclass(frozen=True)
class RubricSnapshot:
    title: str
    points_total: float
    criteria: List[RubricCriterion]


@dataclass(frozen=True)
class GradeRun:
    preflight: PreflightSummary
    rubric: RubricSnapshot
    submission_text: str
    submission_word_count: int


# -----------------------------
# AIGrader
# -----------------------------

class AIGrader:
    """
    Orchestrates a grading run.

    Current phase:
      - Validates assignment exists
      - Validates rubric exists and has criteria
      - Validates there is a text-entry submission (or finds one if user_id not provided)
      - Normalizes submission HTML -> plain text
      - Normalizes rubric long_description HTML -> plain text
      - Returns a GradeRun (preflight + rubric snapshot + submission snapshot)
    """

    def __init__(self, canvas_client: CanvasClient):
        self.canvas_client = canvas_client

    # -----------------------------
    # Public API
    # -----------------------------

    def grade_assignment(
        self,
        course_id: int,
        assignment_id: int,
        user_id: Optional[int] = None,
    ) -> GradeRun:
        # 1) Assignment
        assignment = self.canvas_client.get_assignment(course_id, assignment_id)
        if not assignment or not assignment.get("id"):
            raise AssignmentError("Assignment not found or not accessible via API.")

        assignment_name = str(assignment.get("name") or f"Assignment {assignment_id}")

        # 2) Rubric (full JSON)
        rubric_json = self.canvas_client.get_rubric_for_assignment(course_id, assignment_id)
        if not rubric_json:
            raise RubricError("No rubric found attached to assignment.")

        rubric_snapshot = self._extract_rubric_snapshot(rubric_json)
        if len(rubric_snapshot.criteria) == 0:
            raise RubricError("Rubric found, but it has no criteria.")

        # 3) Submission (text entry)
        submission = self.canvas_client.get_submission_text_entry(
            course_id, assignment_id, user_id=user_id
        )
        if not submission:
            raise SubmissionError("No submission found for this assignment (or user).")

        submission_user_id = submission.get("user_id")
        if submission_user_id is None:
            raise SubmissionError("Submission returned, but it did not include user_id.")

        body_html = submission.get("body")
        if not isinstance(body_html, str) or not body_html.strip():
            raise SubmissionError("Submission found, but no text-entry body was present.")

        submission_text = html_to_text(body_html)
        submission_wc = word_count(submission_text)

        # 4) Preflight summary
        preflight = PreflightSummary(
            course_id=int(course_id),
            assignment_id=int(assignment_id),
            assignment_name=assignment_name,
            rubric_title=rubric_snapshot.title,
            rubric_criteria_count=len(rubric_snapshot.criteria),
            rubric_points_total=float(rubric_snapshot.points_total),
            submission_user_id=int(submission_user_id),
            submission_word_count=int(submission_wc),
        )

        return GradeRun(
            preflight=preflight,
            rubric=rubric_snapshot,
            submission_text=submission_text,
            submission_word_count=int(submission_wc),
        )

    # -----------------------------
    # Internal helpers
    # -----------------------------

    def _extract_rubric_snapshot(self, rubric_json: Dict[str, Any]) -> RubricSnapshot:
        """
        Normalize Canvas rubric JSON into a stable, model-friendly snapshot.

        Canvas rubric criteria may be in:
          - rubric_json["data"] (list of criteria)  [common]
          - rubric_json["criteria"] (list or dict)
        """
        title = str(rubric_json.get("title") or "Untitled Rubric")

        # points_possible may exist; if not, sum criterion points
        points_total = rubric_json.get("points_possible")

        # Locate raw criteria
        if isinstance(rubric_json.get("data"), list):
            criteria_raw = rubric_json.get("data")
        elif isinstance(rubric_json.get("criteria"), list):
            criteria_raw = rubric_json.get("criteria")
        elif isinstance(rubric_json.get("criteria"), dict):
            criteria_raw = list(rubric_json.get("criteria", {}).values())
        else:
            criteria_raw = []

        criteria: List[RubricCriterion] = []

        for c in criteria_raw:
            if not isinstance(c, dict):
                continue

            cid = c.get("id")
            if cid is None:
                # Cannot write back without criterion id
                continue

            desc = str(c.get("description") or "").strip()

            # Normalize rubric guidance HTML -> plain text
            raw_long_desc = str(c.get("long_description") or "")
            long_desc = html_to_text(raw_long_desc)

            pts_raw = c.get("points", 0)
            try:
                pts = float(pts_raw)
            except Exception:
                pts = 0.0

            criteria.append(
                RubricCriterion(
                    id=str(cid),
                    description=desc,
                    long_description=long_desc,
                    points=float(pts),
                )
            )

        if points_total is None:
            points_total = sum(c.points for c in criteria)

        try:
            points_total_f = float(points_total)
        except Exception:
            points_total_f = float(sum(c.points for c in criteria))

        return RubricSnapshot(
            title=title,
            points_total=points_total_f,
            criteria=criteria,
        )
