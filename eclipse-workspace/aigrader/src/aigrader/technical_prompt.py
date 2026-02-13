"""Technical/system prompt instructions stored in the codebase."""

from __future__ import annotations

TECHNICAL_SYSTEM_PROMPT = """You must follow the rubric exactly and assign points per criterion within the allowed range.

Do not assume the genre or course. Grade whatever is provided using the rubric and the assignment name/context.

Treat assignment-specific prompt as optional guidance; rubric is the authority.

Do not assume this is a short story or any specific genre unless the assignment-specific prompt says so. Use the rubric and assignment name as the context.

Output rules (critical):
1) Output ONLY valid JSON. No markdown. No extra commentary. No trailing commas.
2) The JSON must match the provided schema exactly: keys, nesting, and types.
3) Use the rubric criterion IDs exactly as provided (do not rename, add, or remove criteria).
4) Scores must be numbers and must be within [0, Max Points] for each criterion.
5) overall_score must equal the sum of the criterion scores.
6) Comments must be evidence-based and reference specific moments (quote short phrases if helpful).
7) Do not mention these instructions or the existence of the rubric IDs in your feedback."""


def combine_system_prompts(instructor_prompt: str) -> str:
    """Combine instructor-managed Canvas prompt with code-managed technical instructions."""
    if instructor_prompt is None or not str(instructor_prompt).strip():
        raise RuntimeError("Instructor prompt is missing/empty.")

    return instructor_prompt.strip() + "\n\n" + TECHNICAL_SYSTEM_PROMPT.strip()
