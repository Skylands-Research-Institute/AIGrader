# aigrader-test/driver.py
#
# Phase 3 test driver with full command-line arguments.
# (Regenerated to remove run.rubric.criteria_map dependency.)

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

from aigrader.canvas.client import CanvasAuth, CanvasClient
from aigrader.grader import AIGrader
from aigrader.prompt_builder import build_prompts
from aigrader.score_parser import parse_and_validate


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AIGrader Phase-3 test driver (no LLM call).")

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

    # Build a local map {criterion_id: max_points} for printing and validation context
    max_points_by_id = {c.id: float(c.points) for c in run.rubric.criteria}

    # 3) Mock model JSON (perfect-score example)
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

    mock_text = json.dumps(mock_model_json, indent=2, ensure_ascii=False)

    # 4) Parse + validate
    result = parse_and_validate(mock_text, run)

    print("\n=== PARSED ASSESSMENT RESULT ===")
    print(f"Overall score: {result.overall_score:g}")
    print(f"Overall comment: {result.overall_comment}")
    print("Criteria:")
    for cid, a in result.criteria.items():
        max_pts = max_points_by_id.get(cid, float("nan"))
        max_str = f"{max_pts:g}" if max_pts == max_pts else "?"
        print(f"  - {cid}: {a.score:g} / {max_str}")
        print(f"    {a.comment}")

    print("\nOK: End-to-end prompt + validation pipeline succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
