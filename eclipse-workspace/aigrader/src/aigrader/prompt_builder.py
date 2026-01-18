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
from typing import Dict, List

from .grader import GradeRun, RubricCriterion


@dataclass(frozen=True)
class PromptSpec:
    system_prompt: str
    user_prompt: str
    expected_schema_json: str  # human-readable JSON schema-like template


def build_prompts(run: GradeRun, *, system_prompt: str) -> PromptSpec:
    """
    Build the system and user prompts for rubric-aligned assessment.

    IMPORTANT:
      - `system_prompt` must be provided by the caller (e.g., loaded from Canvas Files).
      - There is no embedded default system prompt in this module.
    """
    if system_prompt is None or not str(system_prompt).strip():
        raise RuntimeError(
            "System prompt is missing/empty. "
            "Expected caller to load it (e.g., Canvas file AIGrader/initial_prompt.txt) "
            "and pass build_prompts(..., system_prompt=...)."
        )

    rubric_block = _format_rubric(run.rubric.title, run.rubric.points_total, run.rubric.criteria)
    submission_block = _format_submission(run.submission_text)
    expected = _expected_output_template(run.rubric.criteria)

    # Try to include assignment name if present (keeps prompt generic but contextual).
    assignment_name = ""
    try:
        # Some implementations store this on run.preflight; others might store on run itself.
        assignment_name = getattr(getattr(run, "preflight", None), "assignment_name", "") or getattr(run, "assignment_name", "")
    except Exception:
        assignment_name = ""

    header_lines: List[str] = []
    header_lines.append("You will grade the following student submission using the rubric provided.")
    if isinstance(assignment_name, str) and assignment_name.strip():
        header_lines.append(f"ASSIGNMENT: {assignment_name.strip()}")

    user_prompt = "\n\n".join(
        [
            "\n".join(header_lines),
            # Keep this instruction here as a belt-and-suspenders, even if system prompt also says it.
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
        system_prompt=system_prompt.strip(),
        user_prompt=user_prompt,
        expected_schema_json=expected,
    )


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
            guidance = "\n".join("  " + ln for ln in c.long_description.strip().splitlines())
            lines.append("  Guidance:")
            lines.append(guidance)
        lines.append("")

    return "\n".join(lines).rstrip()


def _format_submission(text: str) -> str:
    return "STUDENT SUBMISSION (plain text):\n\n" + (text or "").strip()


def _expected_output_template(criteria: List[RubricCriterion]) -> str:
    crit_obj: Dict[str, Dict[str, object]] = {}
    for c in criteria:
        crit_obj[c.id] = {
            "score": 0,
            "comment": "Brief, specific feedback tied to evidence from the submission.",
        }

    template = {
        "overall_score": 0,
        "overall_comment": "2-6 sentences summarizing strengths + 1-2 prioritized next steps.",
        "criteria": crit_obj,
    }

    return json.dumps(template, indent=2, ensure_ascii=False)
