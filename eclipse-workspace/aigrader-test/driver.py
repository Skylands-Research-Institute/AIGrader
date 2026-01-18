# aigrader-test/driver.py
#
# Phase 3 test driver with full command-line arguments.
# Updated: optional real LLM call via --use-llm (skips mock JSON).
# Updated: optional posting of AI assessment as Canvas submission comment via --post-comment.
#
# Usage (mock):
#   python driver.py --course-id 16388 --assignment-id 364682 --user-id 28700 --base-url https://... --token ... --post-comment
#
# Usage (real LLM):
#   set OPENAI_API_KEY=...
#   python driver.py --course-id 16388 --assignment-id 364682 --user-id 28700 --use-llm --post-comment
#
# Notes:
#  - --token may be omitted if CANVAS_TOKEN env var is set
#  - --base-url may be omitted if CANVAS_BASE_URL env var is set
#  - --openai-model may be omitted if OPENAI_MODEL env var is set (defaults in LLMClient)

import argparse
import json
import os
import sys
from pathlib import Path

# --- Make aigrader package visible ---
HERE = Path(__file__).resolve()
WORKSPACE = HERE.parents[1]  # .../eclipse-workspace/
AIGRADER_SRC = WORKSPACE / "aigrader" / "src"
if AIGRADER_SRC.exists():
    sys.path.insert(0, str(AIGRADER_SRC))

from aigrader.assessment_comment import CommentMetadata, render_ai_assessment_comment
from aigrader.canvas.client import CanvasAuth, CanvasClient
from aigrader.grader import AIGrader
from aigrader.prompt_builder import build_prompts
from aigrader.score_parser import parse_and_validate

# Only import OpenAI wrapper if needed
try:
    from aigrader.llm import LLMClient
except Exception:
    LLMClient = None  # type: ignore


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AIGrader Phase-3 test driver (mock or real LLM).")

    p.add_argument(
        "--base-url",
        default=None,
        help="Canvas base URL (e.g. https://sussexccc.instructure.com). Defaults to CANVAS_BASE_URL env var.",
    )
    p.add_argument(
        "--token",
        default=None,
        help="Canvas API token. Defaults to CANVAS_TOKEN env var.",
    )
    p.add_argument("--course-id", type=int, required=True, help="Canvas course ID.")
    p.add_argument("--assignment-id", type=int, required=True, help="Canvas assignment ID.")
    p.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Canvas user ID (optional; if omitted, most recent submission is used).",
    )
    p.add_argument(
        "--print-prompts",
        action="store_true",
        help="Print full system and user prompts (not truncated).",
    )

    # --- LLM options ---
    p.add_argument(
        "--use-llm",
        action="store_true",
        help="Call the real LLM (OpenAI) instead of using mocked JSON.",
    )
    p.add_argument(
        "--openai-model",
        default=None,
        help="OpenAI model name (defaults to OPENAI_MODEL env var or LLMClient default).",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Sampling temperature (optional). For grading, 0.0–0.3 is typical.",
    )
    p.add_argument(
        "--reasoning-effort",
        default=None,
        help="Optional reasoning effort hint (e.g., low/medium/high) for supported models.",
    )
    p.add_argument(
        "--save-raw",
        default=None,
        help="Optional path to save the raw model output text (useful for debugging).",
    )

    # --- Canvas writeback option (comment only) ---
    p.add_argument(
        "--post-comment",
        action="store_true",
        help="Post the parsed assessment as a Canvas submission comment (review-only; does not apply grade/rubric).",
    )

    return p.parse_args()


def main() -> int:
    args = parse_args()

    base_url = (args.base_url or os.getenv("CANVAS_BASE_URL") or "").strip()
    token = (args.token or os.getenv("CANVAS_TOKEN") or "").strip()

    if not base_url:
        raise RuntimeError("Canvas base URL not provided (--base-url or CANVAS_BASE_URL).")
    if not token:
        raise RuntimeError("Canvas token not provided (--token or CANVAS_TOKEN).")

    client = CanvasClient(CanvasAuth(base_url=base_url, token=token))
    grader = AIGrader(client)

    # 1) Build GradeRun
    run = grader.grade_assignment(
        course_id=args.course_id,
        assignment_id=args.assignment_id,
        user_id=args.user_id,
    )

    print("\n=== PREFLIGHT SUMMARY ===")
    print(run.preflight)

    # 2) Build prompts
    spec = build_prompts(run)

    if args.print_prompts:
        print("\n=== SYSTEM PROMPT ===")
        print(spec.system_prompt)
        print("\n=== USER PROMPT ===")
        print(spec.user_prompt)
    else:
        print("\n=== SYSTEM PROMPT (preview) ===")
        print(spec.system_prompt[:800] + ("...\n" if len(spec.system_prompt) > 800 else ""))
        print("\n=== USER PROMPT (preview) ===")
        print(spec.user_prompt[:1200] + ("...\n" if len(spec.user_prompt) > 1200 else ""))

    # Build a local map {criterion_id: max_points} for printing context
    max_points_by_id = {c.id: float(c.points) for c in run.rubric.criteria}

    meta: CommentMetadata | None = None

    # 3) Get model output (real or mock)
    if args.use_llm:
        if LLMClient is None:
            raise RuntimeError(
                "Could not import aigrader.llm.LLMClient. "
                "Ensure the aigrader package is on PYTHONPATH and openai is installed."
            )

        llm = LLMClient(model=args.openai_model)  # api key read from OPENAI_API_KEY
        print("\n=== CALLING LLM ===")
        resp = llm.generate(
            system_prompt=spec.system_prompt,
            user_prompt=spec.user_prompt,
            reasoning_effort=args.reasoning_effort,
            temperature=args.temperature,
        )

        raw_text = resp.text
        print(f"LLM response_id={resp.response_id} model={resp.model}")
        if resp.usage:
            print(f"LLM usage={resp.usage}")

        meta = CommentMetadata(model=resp.model, response_id=resp.response_id)

        if args.save_raw:
            with open(args.save_raw, "w", encoding="utf-8") as f:
                f.write(raw_text)
            print(f"Saved raw model output to: {args.save_raw}")

    else:
        # Mock model JSON (perfect-score example)
        criteria_obj = {}
        total = 0.0
        for c in run.rubric.criteria:
            criteria_obj[c.id] = {
                "score": float(c.points),
                "comment": f"Strong work on {c.description.lower()}; clear evidence throughout.",
            }
            total += float(c.points)

        mock_model_json = {
            "overall_score": total,
            "overall_comment": (
                "This is a polished, engaging short story with a clear narrative arc, "
                "effective use of symbolism, and a consistent voice. To improve further, "
                "consider tightening a few longer sentences and adding one more concrete "
                "sensory detail at the midpoint to heighten tension."
            ),
            "criteria": criteria_obj,
        }

        raw_text = json.dumps(mock_model_json, indent=2, ensure_ascii=False)

    # 4) Parse + validate
    result = parse_and_validate(raw_text, run)

    print("\n=== PARSED ASSESSMENT RESULT ===")
    print(f"Overall score: {result.overall_score:g}")
    print(f"Overall comment: {result.overall_comment}")
    print("Criteria:")
    for cid, a in result.criteria.items():
        max_pts = max_points_by_id.get(cid, float("nan"))
        max_str = f"{max_pts:g}" if max_pts == max_pts else "?"
        print(f"  - {cid}: {a.score:g} / {max_str}")
        print(f"    {a.comment}")

    # 5) Optional: post as Canvas submission comment (review-only)
    if args.post_comment:
        comment_text = render_ai_assessment_comment(run, result, meta=meta)
        client.add_submission_comment(
            course_id=run.preflight.course_id,
            assignment_id=run.preflight.assignment_id,
            user_id=run.preflight.submission_user_id,
            text_comment=comment_text,
            as_html=False,
        )
        print("\nPosted AI assessment as a Canvas submission comment (Not Applied).")

    print("\nOK: End-to-end prompt + validation pipeline succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
