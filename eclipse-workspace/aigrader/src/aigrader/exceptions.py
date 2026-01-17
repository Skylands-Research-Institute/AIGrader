class AIGraderError(Exception):
    """Base exception for AIGrader."""


class PreflightError(AIGraderError):
    """Raised when assignment prerequisites are not met."""


class SubmissionError(AIGraderError):
    """Raised when a valid submission cannot be extracted."""


class RubricError(AIGraderError):
    """Raised when rubric is missing or malformed."""


class EvaluationError(AIGraderError):
    """Raised when LLM evaluation fails or returns invalid output."""
