# src/aigrader/grader.py
#
# Regenerated full file:
# - Supports Online Text Entry submissions (body HTML -> plain text)
# - Supports DOCX file upload submissions (attachments -> download -> extract_docx -> plain text)
# - Still normalizes rubric long_description via html_to_text
# - Returns GradeRun with clean rubric guidance text
#
# Phase 2.7: Preflight + rubric snapshot (normalized) + submission snapshot (text-entry OR docx).
# No OpenAI, no Canvas writeback.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .canvas import CanvasClient
from .exceptions import AssignmentNotFoundError, RubricError, SubmissionNotFoundError
from .extract_docx import extract_docx_text
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
      - Validates there is a submission (text-entry body OR supported .docx attachment)
      - Normalizes submission into plain text
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
            raise AssignmentNotFoundError("Assignment not found or not accessible via API.")

        assignment_name = str(assignment.get("name") or f"Assignment {assignment_id}")

        # 2) Rubric (full JSON)
        rubric_json = self._get_rubric_for_assignment(course_id, assignment_id)
        if not rubric_json:
            raise RubricError("No rubric found attached to assignment.")

        rubric_snapshot = self._extract_rubric_snapshot(rubric_json)
        if len(rubric_snapshot.criteria) == 0:
            raise RubricError("Rubric found, but it has no criteria.")

        # 3) Submission (text-entry OR docx upload)
        submission = self.canvas_client.get_submission_text_entry(
            course_id, assignment_id, user_id=user_id
        )
        if not submission:
            raise SubmissionNotFoundError("No submission found for this assignment (or user).")

        submission_user_id = submission.get("user_id")
        if submission_user_id is None:
            raise SubmissionNotFoundError("Submission returned, but it did not include user_id.")

        submission_text = self._extract_submission_text(submission)
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

    def _get_rubric_for_assignment(self, course_id: int, assignment_id: int) -> Optional[Dict[str, Any]]:
        """
        Maintain compatibility with older CanvasClient method names.
        """
        # Preferred (existing in your original codebase)
        fn = getattr(self.canvas_client, "get_rubric_for_assignment", None)
        if callable(fn):
            data = fn(course_id, assignment_id)
            return data if isinstance(data, dict) else None

        # Alternate name (some refactors used this)
        fn2 = getattr(self.canvas_client, "get_rubric", None)
        if callable(fn2):
            data = fn2(course_id, assignment_id)
            return data if isinstance(data, dict) else None

        raise RubricError("CanvasClient has no get_rubric_for_assignment() or get_rubric() method.")

    def _extract_submission_text(self, submission: Dict[str, Any]) -> str:
        """
        Extract and normalize submission to plain text.
        Priority:
          1) Online text entry: submission["body"] (HTML)
          2) DOCX attachment: first *.docx attachment in submission["attachments"]
        """
        # 1) Online text entry
        body_html = submission.get("body")
        if isinstance(body_html, str) and body_html.strip():
            return html_to_text(body_html)

        # 2) DOCX attachment upload
        attachments = submission.get("attachments")
        if isinstance(attachments, list):
            att = self._pick_first_docx_attachment(attachments)
            if att is not None:
                docx_bytes = self._download_attachment_bytes(att)
                result = extract_docx_text(docx_bytes, include_tables=True, include_headers_footers=False)
                text = result.text.strip()
                if not text:
                    raise SubmissionNotFoundError("DOCX attachment was found, but extracted text was empty.")
                return text

        raise SubmissionNotFoundError(
            "Submission found, but no online text-entry body or supported DOCX attachment was present."
        )

    def _pick_first_docx_attachment(self, attachments: List[Any]) -> Optional[Dict[str, Any]]:
        """
        Return the first attachment dict that appears to be a DOCX file.
        """
        for a in attachments:
            if not isinstance(a, dict):
                continue

            filename = a.get("filename") or a.get("display_name") or ""
            if isinstance(filename, str) and filename.lower().endswith(".docx"):
                return a

            # Some Canvas instances include content-type-like hints
            ctype = a.get("content-type") or a.get("content_type") or ""
            if isinstance(ctype, str) and "officedocument.wordprocessingml.document" in ctype.lower():
                return a

        return None

    def _download_attachment_bytes(self, attachment: Dict[str, Any]) -> bytes:
        """
        Download attachment bytes via CanvasClient.
        Compatible with either:
          - CanvasClient.download_file_bytes(url)
          - Falling back to CanvasClient.session.get(url)
        """
        # Canvas attachment objects commonly have "url"; some have "download_url"
        url = attachment.get("url") or attachment.get("download_url")
        if not isinstance(url, str) or not url.strip():
            # Sometimes only an id is available, and you must fetch /files/:id
            fid = attachment.get("id")
            if fid is not None:
                file_detail = getattr(self.canvas_client, "_request")(
                    "GET",
                    f"/api/v1/files/{fid}",
                )
                if isinstance(file_detail, dict):
                    url = file_detail.get("url") or file_detail.get("download_url")

        if not isinstance(url, str) or not url.strip():
            raise SubmissionNotFoundError("DOCX attachment metadata did not include a usable download URL.")

        # Prefer the helper if present (you added this in the updated client.py)
        dl = getattr(self.canvas_client, "download_file_bytes", None)
        if callable(dl):
            return dl(url)

        # Fallback: use session directly (older clients)
        sess = getattr(self.canvas_client, "session", None)
        timeout = getattr(self.canvas_client, "timeout_s", 30)
        if sess is None:
            raise SubmissionNotFoundError("CanvasClient cannot download attachment: no session present.")

        resp = sess.get(url, timeout=timeout, allow_redirects=True)
        if getattr(resp, "status_code", 500) >= 400:
            raise SubmissionNotFoundError(f"Failed to download DOCX attachment (HTTP {resp.status_code}).")

        data = resp.content
        if not data:
            raise SubmissionNotFoundError("Downloaded DOCX attachment was empty.")
        return data

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
