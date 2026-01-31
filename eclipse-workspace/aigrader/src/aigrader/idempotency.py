"""
Idempotency tracking for grading submissions.

Prevents re-grading submissions that haven't changed.
"""

import hashlib
from typing import Any, Dict, List, Optional


FINGERPRINT_PREFIX = "aigrader_fingerprint:"


def _sha256_text(s: str) -> str:
    """Compute SHA-256 hash of a string."""
    h = hashlib.sha256()
    h.update(s.encode("utf-8"))
    return h.hexdigest()


def compute_submission_fingerprint(submission: Dict[str, Any]) -> str:
    """
    Compute a fingerprint for a submission based on its content and metadata.
    
    The fingerprint includes:
    - Attempt number (if available)
    - Submission timestamp
    - Update timestamp
    - SHA-256 hash of submission body
    
    Args:
        submission: Canvas submission object
        
    Returns:
        Fingerprint string
    """
    attempt = submission.get("attempt")
    submitted_at = submission.get("submitted_at") or submission.get("posted_at") or ""
    updated_at = submission.get("updated_at") or ""
    body = submission.get("body") or ""
    
    if not isinstance(body, str):
        body = ""
    
    body_hash = _sha256_text(body)

    if attempt is None:
        return f"attempt=?|submitted_at={submitted_at}|updated_at={updated_at}|body_sha256={body_hash}"
    return f"attempt={attempt}|submitted_at={submitted_at}|updated_at={updated_at}|body_sha256={body_hash}"


def already_assessed(submission_with_comments: Dict[str, Any], fingerprint: str) -> bool:
    """
    Check if a submission has already been assessed with the given fingerprint.
    
    Args:
        submission_with_comments: Canvas submission with comments included
        fingerprint: Fingerprint to search for
        
    Returns:
        True if an assessment with this fingerprint exists
    """
    comments = submission_with_comments.get("submission_comments") or []
    if not isinstance(comments, list):
        return False

    needle = f"{FINGERPRINT_PREFIX} {fingerprint}"
    for c in comments:
        if not isinstance(c, dict):
            continue
        txt = c.get("comment") or ""
        if isinstance(txt, str) and needle in txt:
            return True
    return False


def get_fingerprint_marker(fingerprint: str) -> str:
    """
    Get the marker text to include in assessment comments.
    
    Args:
        fingerprint: Submission fingerprint
        
    Returns:
        Marker text to append to comments
    """
    return f"{FINGERPRINT_PREFIX} {fingerprint}"


class SubmissionTracker:
    """
    Helper class for tracking submission assessment state.
    """
    
    def __init__(self):
        self.fingerprint_prefix = FINGERPRINT_PREFIX
    
    def compute_fingerprint(self, submission: Dict[str, Any]) -> str:
        """Compute fingerprint for a submission."""
        return compute_submission_fingerprint(submission)
    
    def has_been_assessed(
        self, 
        submission_with_comments: Dict[str, Any], 
        fingerprint: str
    ) -> bool:
        """Check if submission has been assessed."""
        return already_assessed(submission_with_comments, fingerprint)
    
    def get_marker(self, fingerprint: str) -> str:
        """Get fingerprint marker for comments."""
        return get_fingerprint_marker(fingerprint)
