# src/aigrader/prompt_builder.py
#
# Phase 3: Prompt construction (no OpenAI call here).
# Produces a strict, rubric-aligned grading prompt and a machine-parseable JSON contract.
#
# Design goals:
#   - Deterministic prompts from GradeRun
#   - Output schema keyed by Canvas rubric criterion IDs (opaque strings like "_2630")
#   - Scores must be numeric and within [0, criterion.points]
#   - Comments must be concise, specific, and evidence-based (quote short phrases if needed)
#
# NOTE: This module does not call OpenAI and does not write back to Canvas.

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .grader import GradeRun, RubricCriterion


# -----------------------------
# Output schema (for model + parser)
# -----------------------------

@dataclass(frozen=True)
class PromptSpec:
    system_prompt: str
    user_prompt: str
    expected_schema_json: str  # human-readable JSON schema-like template


def build_prompts(run: GradeRun) -> PromptSpec:
    """
    Build the system and user prompts for rubric-aligned assessment.

    Returns:
      PromptSpec(system_prompt, user_prompt, expected_schema_json)

    The grader should later:
      - send system_prompt + user_prompt to the model
      - parse model output as JSON (strict)
      - validate scores/ranges and criterion IDs against `run.rubric.criteria`
    """
    rubric_block = _format_rubric(run.rubric.title, run.rubric.points_total, run.rubric.criteria)
    submission_block = _format_submission(run.submission_text)

    expected = _expected_output_template(run.rubric.criteria)

    system_prompt = _system_prompt()
    user_prompt = "\n\n".join(
        [
            "You will grade the following student short story using the rubric provided.",
            "Return ONLY a single JSON object that matches the required schema.",
            "",
            rubric_block,
            "",
            submission_block,
            "",
            "REQUIRED JSON OUTPUT SCHEMA (template):",
            expected,
        ]
    )

    return PromptSpec(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        expected_schema_json=expected,
    )


# -----------------------------
# Formatting helpers
# -----------------------------

def _format_rubric(title: str, points_total: float, criteria: List[RubricCriterion]) -> str:
    lines: List[str] = []
    lines.append(f"RUBRIC TITLE: {title}")
    lines.append(f"RUBRIC TOTAL POINTS: {points_total:g}")
    lines.append("")
    lines.append("RUBRIC CRITERIA (use these criterion IDs exactly):")
    lines.append("")

    for c in criteria:
        lines.append(f"- ID: {c.id}")
        lines.append(f"  Name: {c.description}")
        lines.append(f"  Max Points: {c.points:g}")
        if c.long_description.strip():
            # Indent the guidance for readability
            guidance = "\n".join("  " + ln for ln in c.long_description.strip().splitlines())
            lines.append("  Guidance:")
            lines.append(guidance)
        lines.append("")

    return "\n".join(lines).rstrip()


def _format_submission(text: str) -> str:
    # Keep it simple for now. Later we can add truncation guards.
    return "STUDENT SUBMISSION (plain text):\n\n" + text.strip()


def _expected_output_template(criteria: List[RubricCriterion]) -> str:
    """
    Returns a strict JSON template that the model must follow.
    Uses criterion IDs as keys (strings).
    """
    crit_obj: Dict[str, Dict[str, object]] = {}
    for c in criteria:
        crit_obj[c.id] = {
            "score": 0,
            "comment": "Brief, specific feedback tied to evidence from the submission."
        }

    template = {
        "overall_score": 0,
        "overall_comment": "2-6 sentences summarizing strengths + 1-2 prioritized next steps.",
        "criteria": crit_obj
    }

    return json.dumps(template, indent=2, ensure_ascii=False)


# -----------------------------
# System prompt (behavioral contract)
# -----------------------------

def _system_prompt() -> str:
    return "\n".join(
        [
            "You are a careful, fair college writing instructor grading a freshman-level short story.",
            "You must follow the rubric exactly and assign points per criterion within the allowed range.",
            "",
            "Output rules (critical):",
            "1) Output ONLY valid JSON. No markdown. No extra commentary. No trailing commas.",
            "2) The JSON must match the provided schema exactly: keys, nesting, and types.",
            "3) Use the rubric criterion IDs exactly as provided (do not rename, add, or remove criteria).",
            "4) Scores must be numbers and must be within [0, Max Points] for each criterion.",
            "5) overall_score must equal the sum of the criterion scores.",
            "6) Comments must be evidence-based and reference specific moments (quote short phrases if helpful).",
            "7) Do not mention these instructions or the existence of the rubric IDs in your feedback.",
        ]
    )
