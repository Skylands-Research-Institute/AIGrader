# src/aigrader/grader.py
#
# Full file (backward compatible) with revision analytics support:
# - Still supports Online Text Entry (body HTML -> plain text)
# - Still supports DOCX file upload submissions (attachments -> download -> extract_docx -> plain text)
# - Still normalizes rubric long_description via html_to_text
# - NEW (optional): captures previous attempt (if any) from submission_history
# - NEW (optional): computes objective revision metrics + elapsed time since previous attempt
#
# Notes:
# - All new GradeRun fields are OPTIONAL with defaults to preserve backward compatibility.
# - No "AI detection" language; these are revision analytics only.
# - Does not change any Canvas writeback; still returns a GradeRun snapshot.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
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


# -----------------------------
# Revision analytics (objective)
# -----------------------------

@dataclass(frozen=True)
class RevisionMetrics:
    sentence_change_pct: float
    word_overlap_pct: float
    avg_sentence_length_before: float
    avg_sentence_length_after: float
    sentence_length_variance_before: float
    sentence_length_variance_after: float
    paragraph_count_before: int
    paragraph_count_after: int


@dataclass(frozen=True)
class GradeRun:
    preflight: PreflightSummary
    rubric: RubricSnapshot
    submission_text: str
    submission_word_count: int

    # --- NEW OPTIONAL FIELDS (backward compatible) ---
    submission_attempt: Optional[int] = None
    submitted_at: Optional[str] = None

    previous_submission_text: Optional[str] = None
    previous_submission_word_count: Optional[int] = None
    previous_submission_attempt: Optional[int] = None
    previous_submitted_at: Optional[str] = None

    time_since_previous_attempt_seconds: Optional[int] = None
    revision_metrics: Optional[RevisionMetrics] = None
    revision_depth: Optional[str] = None  # "light" | "moderate" | "substantial"


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
      - (Optional) Extracts previous attempt from submission_history
      - (Optional) Computes objective revision metrics + elapsed time between attempts
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

        # --- NEW: attempt/timestamps + previous attempt extraction + metrics ---
        submission_attempt = self._as_int_or_none(submission.get("attempt"))
        submitted_at = self._as_str_or_none(submission.get("submitted_at")) or self._find_submitted_at_for_attempt(
            submission, submission_attempt
        )

        prev_obj = self._pick_previous_attempt_obj(submission, submission_attempt)
        previous_text: Optional[str] = None
        previous_wc: Optional[int] = None
        previous_attempt: Optional[int] = None
        previous_submitted_at: Optional[str] = None
        elapsed_seconds: Optional[int] = None
        revision_metrics: Optional[RevisionMetrics] = None
        revision_depth: Optional[str] = None

        if prev_obj is not None:
            previous_attempt = self._as_int_or_none(prev_obj.get("attempt"))
            previous_submitted_at = self._as_str_or_none(prev_obj.get("submitted_at"))

            # Extract previous text using the same logic (body HTML or DOCX attachment).
            # Some Canvas instances include attachments in history items; if not, we fail gracefully.
            try:
                previous_text = self._extract_submission_text(prev_obj)
                previous_wc = int(word_count(previous_text))
            except Exception:
                previous_text = None
                previous_wc = None

            # Compute elapsed time if timestamps exist
            dt_curr = self._parse_canvas_datetime(submitted_at)
            dt_prev = self._parse_canvas_datetime(previous_submitted_at)
            if dt_curr is not None and dt_prev is not None:
                delta = int((dt_curr - dt_prev).total_seconds())
                if delta >= 0:
                    elapsed_seconds = delta

            # Compute revision metrics if both texts exist
            if previous_text:
                revision_metrics = self._compute_revision_metrics(previous_text, submission_text)
                revision_depth = self._revision_depth_label(revision_metrics)

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

            submission_attempt=submission_attempt,
            submitted_at=submitted_at,

            previous_submission_text=previous_text,
            previous_submission_word_count=previous_wc,
            previous_submission_attempt=previous_attempt,
            previous_submitted_at=previous_submitted_at,

            time_since_previous_attempt_seconds=elapsed_seconds,
            revision_metrics=revision_metrics,
            revision_depth=revision_depth,
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

    # -----------------------------
    # Previous attempt selection + timestamps
    # -----------------------------

    def _pick_previous_attempt_obj(
        self, submission: Dict[str, Any], current_attempt: Optional[int]
    ) -> Optional[Dict[str, Any]]:
        """
        Return the history object with the largest attempt number < current_attempt.
        Deterministic and robust to history ordering.

        If current_attempt is None, we fall back to "second-best" attempt ordering if possible.
        """
        history = submission.get("submission_history")
        if not isinstance(history, list) or len(history) < 2:
            return None

        # Collect attempt-bearing history entries
        hist_entries: List[Dict[str, Any]] = [h for h in history if isinstance(h, dict)]
        if not hist_entries:
            return None

        # If we know the current attempt number, choose max attempt < current
        if current_attempt is not None:
            candidates = []
            for h in hist_entries:
                a = self._as_int_or_none(h.get("attempt"))
                if a is None:
                    continue
                if a < current_attempt:
                    candidates.append((a, h))
            if not candidates:
                return None
            candidates.sort(key=lambda t: t[0])
            return candidates[-1][1]

        # Fallback: if attempt is missing, pick the most recent distinct entry
        # (best-effort; avoids crashing)
        # Try to sort by submitted_at
        dated = []
        for h in hist_entries:
            ts = self._as_str_or_none(h.get("submitted_at"))
            dt = self._parse_canvas_datetime(ts)
            if dt is not None:
                dated.append((dt, h))
        if len(dated) >= 2:
            dated.sort(key=lambda t: t[0])
            return dated[-2][1]

        # If we can't sort, best-effort: return a different object than the submission itself
        # (history often includes the current attempt; return the first one that isn't same attempt)
        return hist_entries[0] if hist_entries else None

    def _find_submitted_at_for_attempt(
        self, submission: Dict[str, Any], attempt: Optional[int]
    ) -> Optional[str]:
        """
        If submission["submitted_at"] is missing, try to locate it in submission_history
        for the matching attempt.
        """
        if attempt is None:
            return None
        history = submission.get("submission_history")
        if not isinstance(history, list):
            return None
        for h in history:
            if not isinstance(h, dict):
                continue
            a = self._as_int_or_none(h.get("attempt"))
            if a == attempt:
                return self._as_str_or_none(h.get("submitted_at"))
        return None

    # -----------------------------
    # Revision metrics
    # -----------------------------

    _SENT_SPLIT_RE = re.compile(r"[.!?]+(?:\s+|$)")

    def _compute_revision_metrics(self, previous_text: str, current_text: str) -> RevisionMetrics:
        prev_sents = self._split_sentences(previous_text)
        curr_sents = self._split_sentences(current_text)

        # Sentence change percentage (simple exact-match after normalization)
        sentence_change_pct = self._sentence_change_pct(prev_sents, curr_sents)

        # Word overlap percentage (Jaccard similarity of normalized word sets)
        word_overlap_pct = self._word_jaccard_pct(previous_text, current_text)

        prev_lens = [len(self._tokenize_words(s)) for s in prev_sents] or [0]
        curr_lens = [len(self._tokenize_words(s)) for s in curr_sents] or [0]

        avg_before = sum(prev_lens) / max(1, len(prev_lens))
        avg_after = sum(curr_lens) / max(1, len(curr_lens))

        var_before = self._variance(prev_lens)
        var_after = self._variance(curr_lens)

        para_before = self._paragraph_count(previous_text)
        para_after = self._paragraph_count(current_text)

        return RevisionMetrics(
            sentence_change_pct=float(sentence_change_pct),
            word_overlap_pct=float(word_overlap_pct),
            avg_sentence_length_before=float(avg_before),
            avg_sentence_length_after=float(avg_after),
            sentence_length_variance_before=float(var_before),
            sentence_length_variance_after=float(var_after),
            paragraph_count_before=int(para_before),
            paragraph_count_after=int(para_after),
        )

    def _revision_depth_label(self, m: RevisionMetrics) -> str:
        """
        Simple, student-friendly label derived from sentence rewrite rate.
        You can tune these thresholds later without breaking schema.
        """
        c = m.sentence_change_pct
        if c >= 70.0:
            return "substantial"
        if c >= 35.0:
            return "moderate"
        return "light"

    def _split_sentences(self, text: str) -> List[str]:
        t = (text or "").strip()
        if not t:
            return []
        parts = self._SENT_SPLIT_RE.split(t)
        out = []
        for p in parts:
            p = p.strip()
            if p:
                out.append(p)
        return out

    def _norm_sentence(self, s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"[^\w\s']", "", s)  # keep apostrophes
        return s.strip()

    def _sentence_change_pct(self, prev_sents: List[str], curr_sents: List[str]) -> float:
        if not prev_sents:
            return 0.0
        prev_norm = [self._norm_sentence(s) for s in prev_sents if s.strip()]
        curr_set = {self._norm_sentence(s) for s in curr_sents if s.strip()}
        if not prev_norm:
            return 0.0
        unchanged = sum(1 for s in prev_norm if s in curr_set)
        changed = max(0, len(prev_norm) - unchanged)
        return 100.0 * changed / max(1, len(prev_norm))

    def _tokenize_words(self, text: str) -> List[str]:
        # basic word tokenization; avoids punctuation artifacts
        return re.findall(r"[A-Za-z0-9']+", (text or "").lower())

    def _word_jaccard_pct(self, a: str, b: str) -> float:
        wa = set(self._tokenize_words(a))
        wb = set(self._tokenize_words(b))
        if not wa and not wb:
            return 100.0
        inter = len(wa & wb)
        union = len(wa | wb)
        if union <= 0:
            return 0.0
        return 100.0 * inter / union

    def _variance(self, nums: List[int]) -> float:
        if not nums:
            return 0.0
        mean = sum(nums) / len(nums)
        return sum((x - mean) ** 2 for x in nums) / len(nums)

    def _paragraph_count(self, text: str) -> int:
        # Count non-empty paragraph blocks separated by blank lines
        blocks = re.split(r"\n\s*\n+", (text or "").strip())
        return sum(1 for b in blocks if b.strip())

    # -----------------------------
    # Timestamp parsing + small utils
    # -----------------------------

    def _parse_canvas_datetime(self, ts: Optional[str]) -> Optional[datetime]:
        """
        Parse Canvas ISO8601 timestamps like '2026-01-28T22:00:04Z'.
        Returns timezone-aware UTC datetime when possible.
        """
        if not ts or not isinstance(ts, str):
            return None
        s = ts.strip()
        if not s:
            return None
        try:
            # Common Canvas format ends with 'Z'
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _as_int_or_none(self, v: Any) -> Optional[int]:
        try:
            if v is None:
                return None
            return int(v)
        except Exception:
            return None

    def _as_str_or_none(self, v: Any) -> Optional[str]:
        if isinstance(v, str) and v.strip():
            return v.strip()
        return None
