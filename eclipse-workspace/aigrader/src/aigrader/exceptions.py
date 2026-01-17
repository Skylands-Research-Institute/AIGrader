# aigrader/exceptions.py
"""
Central exception types for AIGrader.

Keep these lightweight and specific so callers can catch/handle different
failure modes cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class AIGraderError(Exception):
    """Base class for all AIGrader exceptions."""


# ----------------------------
# Canvas / data acquisition
# ----------------------------

@dataclass
class CanvasAPIError(AIGraderError):
    method: str
    url: str
    status_code: int
    body: str

    def __str__(self) -> str:
        snippet = self.body
        if len(snippet) > 500:
            snippet = snippet[:500] + "...[truncated]"
        return f"{self.method} {self.url} -> {self.status_code}: {snippet}"


class PreflightError(AIGraderError):
    """Base class for preflight failures (missing rubric/submission/etc.)."""


class AssignmentNotFoundError(PreflightError):
    pass


class SubmissionNotFoundError(PreflightError):
    pass


class SubmissionContentError(PreflightError):
    pass


class RubricError(PreflightError):
    pass


class RubricNotFoundError(RubricError):
    pass


class RubricInvalidError(RubricError):
    pass


# ----------------------------
# Prompt / model / parsing
# ----------------------------

class PromptBuildError(AIGraderError):
    pass


class LLMError(AIGraderError):
    pass


class ModelOutputError(AIGraderError):
    """Model returned output that is invalid or violates required schema."""


class JSONParseError(ModelOutputError):
    pass


class SchemaValidationError(ModelOutputError):
    pass


class ScoreValidationError(ModelOutputError):
    pass


# ----------------------------
# Writeback (future)
# ----------------------------

class WritebackError(AIGraderError):
    pass
