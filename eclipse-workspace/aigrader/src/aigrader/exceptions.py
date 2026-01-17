# src/aigrader/exceptions.py
#
# Regenerated full file: project-specific exception hierarchy for AIGrader.

from __future__ import annotations


class AIGraderError(Exception):
    """Base class for all AIGrader exceptions."""


# -----------------------------
# Canvas / data preflight errors
# -----------------------------

class CanvasClientError(AIGraderError):
    """Errors raised due to Canvas API client failures (HTTP errors, parsing, etc.)."""


class AssignmentError(AIGraderError):
    """Raised when an assignment cannot be fetched or is invalid for grading."""


class RubricError(AIGraderError):
    """Raised when a rubric is missing, malformed, or unsuitable for grading."""


class SubmissionError(AIGraderError):
    """Raised when a submission is missing or not in the expected format."""


# -----------------------------
# Future pipeline errors (placeholders)
# -----------------------------

class PromptError(AIGraderError):
    """Raised when prompt construction fails or required fields are missing."""


class ModelError(AIGraderError):
    """Raised for OpenAI/API/model failures (timeouts, invalid responses, etc.)."""


class ParseError(AIGraderError):
    """Raised when parsing the model output fails or violates the expected schema."""


class WritebackError(AIGraderError):
    """Raised when writing rubric assessments back to Canvas fails."""
