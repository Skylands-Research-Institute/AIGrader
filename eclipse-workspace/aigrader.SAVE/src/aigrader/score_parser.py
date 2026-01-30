# src/aigrader/score_parser.py
#
# Phase 3b: Parse + validate model output (strict JSON) against a GradeRun rubric snapshot.
#
# Responsibilities:
#   - Parse model output as strict JSON
#   - Validate schema (keys/types)
#   - Validate criterion IDs exactly match rubric IDs
#   - Validate per-criterion score ranges
#   - Validate overall_score equals sum of criterion scores (within tolerance)
#
# This module does NOT call OpenAI and does NOT write to Canvas.
# It is intentionally strict to prevent "LLM drift" from breaking grading/writeback.

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from .exceptions import JSONParseError, RubricNotFoundError
from .grader import GradeRun, RubricCriterion


# -----------------------------
# Typed result objects
# -----------------------------

@dataclass(frozen=True)
class CriterionAssessment:
    score: float
    comment: str


@dataclass(frozen=True)
class AssessmentResult:
    overall_score: float
    overall_comment: str
    criteria: Dict[str, CriterionAssessment]


# -----------------------------
# Public API
# -----------------------------

def parse_and_validate(model_text: str, run: GradeRun) -> AssessmentResult:
    """
    Parse model output and validate it against the rubric in `run`.

    Expected JSON structure:
      {
        "overall_score": number,
        "overall_comment": string,
        "criteria": {
          "<criterion_id>": {"score": number, "comment": string},
          ...
        }
      }

    Raises:
      ParseError  - invalid JSON, wrong schema, missing/extra keys, type mismatches
      RubricError - rubric snapshot invalid / empty
    """
    if not run or not run.rubric or not run.rubric.criteria:
        raise RubricNotFoundError("Cannot validate: rubric snapshot is missing or empty.")

    obj = _parse_json_strict(model_text)
    _validate_top_level(obj)

    rubric_map = _rubric_criteria_map(run.rubric.criteria)
    _validate_criteria_keys(obj["criteria"], rubric_map)

    criteria_assessments: Dict[str, CriterionAssessment] = {}
    score_sum = 0.0

    for cid, max_points in rubric_map.items():
        item = obj["criteria"][cid]
        _validate_criterion_item(cid, item)

        score = _as_number(item["score"], f"criteria.{cid}.score")
        _validate_score_range(cid, score, max_points)

        comment = _as_string(item["comment"], f"criteria.{cid}.comment").strip()
        if not comment:
            raise JSONParseError(f"criteria.{cid}.comment must be a non-empty string.")

        criteria_assessments[cid] = CriterionAssessment(score=float(score), comment=comment)
        score_sum += float(score)

    overall_score = _as_number(obj["overall_score"], "overall_score")
    overall_comment = _as_string(obj["overall_comment"], "overall_comment").strip()
    if not overall_comment:
        raise JSONParseError("overall_comment must be a non-empty string.")

    _validate_overall_score(overall_score, score_sum)

    return AssessmentResult(
        overall_score=float(overall_score),
        overall_comment=overall_comment,
        criteria=criteria_assessments,
    )


# -----------------------------
# Parsing + schema validation
# -----------------------------

def _parse_json_strict(model_text: str) -> Dict[str, Any]:
    if not isinstance(model_text, str) or not model_text.strip():
        raise JSONParseError("Model output was empty or not a string.")

    # Enforce: model must return ONLY JSON (no markdown).
    s = model_text.strip()

    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as e:
        # Provide a helpful snippet
        snippet = s[:400].replace("\n", "\\n")
        raise JSONParseError(f"Invalid JSON from model: {e.msg} (pos {e.pos}). Snippet: {snippet}") from e

    if not isinstance(parsed, dict):
        raise JSONParseError("Top-level JSON must be an object (dictionary).")

    return parsed


def _validate_top_level(obj: Mapping[str, Any]) -> None:
    required = {"overall_score", "overall_comment", "criteria"}
    keys = set(obj.keys())

    missing = required - keys
    extra = keys - required

    if missing:
        raise JSONParseError(f"Missing top-level key(s): {sorted(missing)}")
    if extra:
        raise JSONParseError(f"Unexpected top-level key(s): {sorted(extra)}")

    if not isinstance(obj["criteria"], dict):
        raise JSONParseError("criteria must be an object mapping criterion_id -> {score, comment}.")


def _rubric_criteria_map(criteria: list[RubricCriterion]) -> Dict[str, float]:
    m: Dict[str, float] = {}
    for c in criteria:
        cid = str(c.id)
        try:
            max_pts = float(c.points)
        except Exception:
            max_pts = 0.0
        m[cid] = max_pts
    return m


def _validate_criteria_keys(criteria_obj: Mapping[str, Any], rubric_map: Dict[str, float]) -> None:
    got = set(criteria_obj.keys())
    expected = set(rubric_map.keys())

    missing = expected - got
    extra = got - expected

    if missing:
        raise JSONParseError(f"criteria is missing rubric criterion id(s): {sorted(missing)}")
    if extra:
        raise JSONParseError(f"criteria contains unexpected criterion id(s): {sorted(extra)}")


def _validate_criterion_item(cid: str, item: Any) -> None:
    if not isinstance(item, dict):
        raise JSONParseError(f"criteria.{cid} must be an object with keys {{score, comment}}.")

    required = {"score", "comment"}
    keys = set(item.keys())

    missing = required - keys
    extra = keys - required

    if missing:
        raise JSONParseError(f"criteria.{cid} missing key(s): {sorted(missing)}")
    if extra:
        raise JSONParseError(f"criteria.{cid} has unexpected key(s): {sorted(extra)}")


# -----------------------------
# Type helpers + numeric validation
# -----------------------------

def _as_string(val: Any, path: str) -> str:
    if not isinstance(val, str):
        raise JSONParseError(f"{path} must be a string.")
    return val


def _as_number(val: Any, path: str) -> float:
    # Allow int/float (and numeric strings only if you want; for now: strict numeric types)
    if isinstance(val, bool):
        raise JSONParseError(f"{path} must be a number, not boolean.")
    if isinstance(val, (int, float)):
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            raise JSONParseError(f"{path} must be a finite number.")
        return float(val)
    raise JSONParseError(f"{path} must be a number.")


def _validate_score_range(cid: str, score: float, max_points: float) -> None:
    if score < 0:
        raise JSONParseError(f"criteria.{cid}.score must be >= 0.")
    # allow a tiny epsilon for float noise
    if score > max_points + 1e-9:
        raise JSONParseError(f"criteria.{cid}.score exceeds max points ({max_points:g}).")


def _validate_overall_score(overall_score: float, score_sum: float) -> None:
    # Use a small tolerance; overall_score should match sum exactly in practice.
    if abs(overall_score - score_sum) > 1e-6:
        raise JSONParseError(
            f"overall_score ({overall_score:g}) does not equal sum of criterion scores ({score_sum:g})."
        )
