"""
AIGrader package.

High-level orchestration for AI-assisted grading with Canvas LMS.
"""

from .grader import AIGrader, GradeRun, PreflightSummary, RubricSnapshot, RubricCriterion
from .canvas import CanvasClient, CanvasAuth
from .exceptions import (
    AssignmentNotFoundError,
    RubricError,
    SubmissionNotFoundError,
)

try:
    from .llm import LLMClient
except ImportError:
    LLMClient = None  # type: ignore

__version__ = "0.1.0"

__all__ = [
    "AIGrader",
    "GradeRun",
    "PreflightSummary",
    "RubricSnapshot",
    "RubricCriterion",
    "CanvasClient",
    "CanvasAuth",
    "LLMClient",
    "AssignmentNotFoundError",
    "RubricError",
    "SubmissionNotFoundError",
]
