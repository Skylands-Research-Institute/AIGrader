"""
AIGrader command-line interface.

Refactored to use new modular components for better maintainability.

CHANGE (2026-02-09):
- If --user-id is NOT provided, the CLI now grades *all* submitted students for the given
  course_id + assignment_id (or for each assignment in --assignment-file).
"""

import argparse
import json
import os
import sys
from typing import Iterable, Optional, List, Set


# Handle both direct execution and module import
if __name__ == "__main__" and __package__ is None:
    # Running directly - add parent directories to path
    from pathlib import Path

    file_path = Path(__file__).resolve()
    src_dir = file_path.parents[2]  # Go up to src/
    if src_dir.exists():
        sys.path.insert(0, str(src_dir))

    # Now import as if we're in the package
    from aigrader.assessment_comment import (
        CommentMetadata,
        render_ai_assessment_comment,
        render_ai_assessment_comment_html,
    )
    from aigrader.batch import AssignmentSpec, BatchGrader
    from aigrader.canvas import CanvasAuth, CanvasClient
    from aigrader.formatting import format_assignment_description_section
    from aigrader.grader import AIGrader
    from aigrader.idempotency import (
        compute_submission_fingerprint,
        already_assessed,
        get_fingerprint_marker,
    )
    from aigrader.prompt_builder import build_prompts
    from aigrader.score_parser import parse_and_validate

    try:
        from aigrader.llm import LLMClient
    except Exception:
        LLMClient = None  # type: ignore
else:
    # Running as module - use relative imports
    from ..assessment_comment import (
        CommentMetadata,
        render_ai_assessment_comment,
        render_ai_assessment_comment_html,
    )
    from ..batch import AssignmentSpec, BatchGrader
    from ..canvas import CanvasAuth, CanvasClient
    from ..formatting import format_assignment_description_section
    from ..grader import AIGrader
    from ..idempotency import (
        compute_submission_fingerprint,
        already_assessed,
        get_fingerprint_marker,
    )
    from ..prompt_builder import build_prompts
    from ..score_parser import parse_and_validate

    try:
        from ..llm import LLMClient
    except Exception:
        LLMClient = None  # type: ignore


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(description="AIGrader - AI-assisted grading for Canvas LMS")

    # Canvas authentication
    p.add_argument("--base-url", default=None, help="Canvas base URL")
    p.add_argument("--token", default=None, help="Canvas API token")

    # Single-assignment mode
    p.add_argument("--course-id", type=int, required=False, help="Canvas course ID")
    p.add_argument("--assignment-id", type=int, required=False, help="Canvas assignment ID")
    p.add_argument("--user-id", type=int, default=None, help="Specific user to grade (optional)")

    # Multi-assignment mode
    p.add_argument(
        "--assignment-file",
        default=None,
        help="Path to TSV/CSV file with columns: course_id, assignment_id, enabled, model, notes",
    )

    # LLM configuration
    p.add_argument("--use-llm", action="store_true", help="Use actual LLM (vs mock mode)")
    p.add_argument("--openai-model", default=None, help="OpenAI model to use")
    p.add_argument("--openai-key", default=None, help="OpenAI API key")
    p.add_argument("--temperature", type=float, default=None, help="LLM temperature")
    p.add_argument(
        "--reasoning-effort",
        default="low",
        choices=["low", "medium", "high"],
        help="Reasoning effort level for LLM",
    )

    # Output options
    p.add_argument("--post-comment", action="store_true", help="Post assessment as Canvas comment")
    p.add_argument("--comment-html", action="store_true", help="Use HTML format for comments")
    p.add_argument("--print-prompts", action="store_true", help="Print prompts instead of grading")
    p.add_argument("--save-raw", default=None, help="Save raw LLM response to file")

    # Control options
    p.add_argument(
        "--force",
        action="store_true",
        help="Grade even if already assessed (ignore idempotency)",
    )

    return p.parse_args()


def _list_submitted_user_ids(client: CanvasClient, course_id: int, assignment_id: int) -> List[int]:
    """
    Return user_ids for submissions that look gradeable.

    "Gradeable" here means:
      - has non-empty online text body, OR
      - has attachments, OR
      - has a submission_type that indicates something was submitted

    We also ignore "unsubmitted" if workflow_state is present.
    """
    include = ["submission_history", "user", "attachments"]
    # NOTE: Uses CanvasClient._get_paginated() (internal helper) to avoid changing CanvasClient.
    subs = client._get_paginated(  # type: ignore[attr-defined]
        f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions",
        params={"include[]": include, "per_page": 100},
    )

    out: Set[int] = set()

    for s in subs or []:
        uid = s.get("user_id")
        if not isinstance(uid, int):
            continue

        workflow = s.get("workflow_state")
        if isinstance(workflow, str) and workflow.lower() in {"unsubmitted"}:
            continue

        body = s.get("body")
        has_body = isinstance(body, str) and body.strip() != ""

        attachments = s.get("attachments")
        has_attachments = isinstance(attachments, list) and len(attachments) > 0

        submission_type = s.get("submission_type")
        has_type = isinstance(submission_type, str) and submission_type.strip() != ""

        if has_body or has_attachments or has_type:
            out.add(uid)

    return sorted(out)


def _save_raw_text(save_raw: str, raw_text: str, course_id: int, assignment_id: int, user_id: int) -> str:
    """
    Save raw output; include user_id in filename to avoid collisions when grading many students.
    Returns the resolved path.
    """
    save_path = save_raw

    # Allow templating: e.g. --save-raw "raw_{course_id}_{assignment_id}_{user_id}.txt"
    if "{" in save_path:
        save_path = save_path.format(course_id=course_id, assignment_id=assignment_id, user_id=user_id)
    else:
        root, ext = os.path.splitext(save_path)
        if ext:
            save_path = f"{root}_{course_id}_{assignment_id}_{user_id}{ext}"
        else:
            save_path = f"{save_path}_{course_id}_{assignment_id}_{user_id}.txt"

    with open(save_path, "w", encoding="utf-8") as f:
        f.write(raw_text)

    return save_path


def _grade_one_submission(
    args: argparse.Namespace,
    client: CanvasClient,
    grader: AIGrader,
    course_id: int,
    assignment_id: int,
    user_id: int,
    model_override: Optional[str] = None,
) -> int:
    """
    Grade exactly one user's submission for one assignment.

    Returns:
        0 on success, non-zero on failure
    """
    print(f"\n{'-'*60}")
    print(f"Student: user_id={user_id}")
    print(f"{'-'*60}")

    # -----------------------------
    # Preflight: validate and extract
    # -----------------------------
    run = grader.grade_assignment(
        course_id=course_id,
        assignment_id=assignment_id,
        user_id=user_id,
    )

    print(f"Assignment: {run.preflight.assignment_name}")
    print(f"Rubric: {run.preflight.rubric_title}")
    print(f"Criteria: {run.preflight.rubric_criteria_count}")
    print(f"Points: {run.preflight.rubric_points_total}")
    print(f"Submission: {run.preflight.submission_word_count} words")

    # -----------------------------
    # Fetch system prompt from Canvas
    # -----------------------------
    initial_prompt = client.get_course_file_text(
        course_id=course_id,
        folder_path="AIGrader",
        filename="initial_prompt.txt",
    )
    print("System prompt source: AIGrader/initial_prompt.txt")
    system_prompt_course = initial_prompt.strip()

    # -----------------------------
    # Idempotency check
    # -----------------------------
    sub = client.get_submission_with_comments(
        course_id=course_id,
        assignment_id=assignment_id,
        user_id=run.preflight.submission_user_id,
    )
    fp = compute_submission_fingerprint(sub)

    already = already_assessed(sub, fp)
    if already and not args.force and not args.print_prompts:
        print("SKIP: Submission already assessed (no changes detected)")
        print(f"Fingerprint: {fp}")
        return 0

    # -----------------------------
    # Fetch assignment description
    # -----------------------------
    assignment_description = client.get_assignment_description(course_id=course_id, assignment_id=assignment_id)
    assignment_desc_section = format_assignment_description_section(assignment_description)

    # -----------------------------
    # Build prompts
    # -----------------------------
    spec = build_prompts(run, system_prompt=system_prompt_course)

    # Combine: system prompt + assignment description
    system_prompt_to_send = spec.system_prompt.rstrip() + "\n\n" + assignment_desc_section + "\n"

    # Print prompts if requested
    if args.print_prompts:
        print("\n=== SYSTEM PROMPT ===")
        print(system_prompt_to_send)
        print("\n=== USER PROMPT ===")
        print(spec.user_prompt)
        print("NOTE: --print-prompts enabled (printing prompts, continuing with assessment).")
    else:
        print("\n=== SYSTEM PROMPT (preview) ===")
        preview = system_prompt_to_send[:800]
        if len(system_prompt_to_send) > 800:
            preview += "..."
        print(preview)
        print("\n=== USER PROMPT (preview) ===")
        preview = spec.user_prompt[:1200]
        if len(spec.user_prompt) > 1200:
            preview += "..."
        print(preview)

    # If already assessed and not forced, stop (matches your existing behavior)
    if already and not args.force:
        print("\nSKIP: Already assessed (use --force to regrade)")
        print(f"Fingerprint: {fp}")
        return 0

    meta = CommentMetadata(model=None, response_id=None)

    # -----------------------------
    # Call LLM or use mock
    # -----------------------------
    if args.use_llm:
        if LLMClient is None:
            raise RuntimeError("Could not import aigrader.llm.LLMClient")

        chosen_model = model_override or args.openai_model
        llm = LLMClient(api_key=args.openai_key, model=chosen_model)

        print("\n=== CALLING LLM ===")
        resp = llm.generate(
            system_prompt=system_prompt_to_send,
            user_prompt=spec.user_prompt,
            reasoning_effort=args.reasoning_effort,
            temperature=args.temperature,
        )
        raw_text = resp.text
        meta = CommentMetadata(model=resp.model, response_id=resp.response_id)

        print(f"Response ID: {resp.response_id}")
        print(f"Model: {resp.model}")
        if resp.usage:
            print(f"Usage: {resp.usage}")

        # Save raw output if requested (now includes user_id)
        if args.save_raw:
            saved = _save_raw_text(args.save_raw, raw_text, course_id, assignment_id, user_id)
            print(f"Saved raw output: {saved}")

    else:
        # Mock mode - perfect scores
        criteria_obj = {}
        total = 0.0
        for c in run.rubric.criteria:
            criteria_obj[c.id] = {"score": float(c.points), "comment": f"Strong work on {c.description.lower()}."}
            total += float(c.points)

        raw_text = json.dumps(
            {"overall_score": total, "overall_comment": "Mock assessment - perfect scores.", "criteria": criteria_obj},
            indent=2,
            ensure_ascii=False,
        )

    # -----------------------------
    # Parse and validate
    # -----------------------------
    result = parse_and_validate(raw_text, run)

    print("\n=== ASSESSMENT RESULT ===")
    print(f"Overall score: {result.overall_score}")
    print(f"Overall comment: {result.overall_comment[:200]}...")

    # -----------------------------
    # Post comment to Canvas
    # -----------------------------
    if args.post_comment:
        marker = get_fingerprint_marker(fp)

        if args.comment_html:
            comment = render_ai_assessment_comment_html(run, result, meta=meta)
            comment = comment + f"<p><em>{marker}</em></p>"
            client.add_submission_comment(
                course_id=course_id,
                assignment_id=assignment_id,
                user_id=run.preflight.submission_user_id,
                text_comment=comment,
                as_html=True,
            )
            print("✓ Posted HTML comment to Canvas")
        else:
            comment = render_ai_assessment_comment(run, result, meta=meta)
            comment = comment + "\n\n" + marker
            client.add_submission_comment(
                course_id=course_id,
                assignment_id=assignment_id,
                user_id=run.preflight.submission_user_id,
                text_comment=comment,
                as_html=False,
            )
            print("✓ Posted text comment to Canvas")

    print("✓ SUCCESS")
    print(f"Fingerprint: {fp}")
    return 0


def grade_one_assignment(
    args: argparse.Namespace,
    client: CanvasClient,
    grader: AIGrader,
    course_id: int,
    assignment_id: int,
    model_override: Optional[str] = None,
) -> int:
    """
    Grade a single assignment.

    CHANGE: If args.user_id is None, grade *all* submitted students for this assignment.

    Returns:
        0 on success (even if some students skipped); non-zero if any student fails
    """
    print(f"\n{'='*60}")
    print(f"Grading: course_id={course_id}, assignment_id={assignment_id}")
    print(f"{'='*60}")

    # Determine who to grade
    if args.user_id is not None:
        user_ids = [args.user_id]
        print(f"Mode: single student (--user-id={args.user_id})")
    else:
        user_ids = _list_submitted_user_ids(client, course_id, assignment_id)
        print(f"Mode: all students (found {len(user_ids)} gradeable submissions)")

    if not user_ids:
        print("No gradeable submissions found; nothing to do.")
        return 0

    any_fail = False
    for uid in user_ids:
        try:
            rc = _grade_one_submission(
                args=args,
                client=client,
                grader=grader,
                course_id=course_id,
                assignment_id=assignment_id,
                user_id=uid,
                model_override=model_override,
            )
            if rc != 0:
                any_fail = True
        except Exception as e:
            any_fail = True
            print(f"✗ FAILED for user_id={uid}: {e}")

    return 1 if any_fail else 0


def main() -> int:
    """Main entry point for CLI."""
    args = parse_args()

    # Get Canvas credentials
    base_url = (args.base_url or os.getenv("CANVAS_BASE_URL") or "").strip()
    token = (args.token or os.getenv("CANVAS_TOKEN") or "").strip()

    if not base_url:
        raise RuntimeError("Missing --base-url or CANVAS_BASE_URL environment variable")
    if not token:
        raise RuntimeError("Missing --token or CANVAS_TOKEN environment variable")

    # Initialize clients
    client = CanvasClient(CanvasAuth(base_url=base_url, token=token))
    grader = AIGrader(client)

    # Multi-assignment mode
    if args.assignment_file:
        batch_grader = BatchGrader(verbose=True)

        def grade_callback(spec: AssignmentSpec) -> int:
            return grade_one_assignment(
                args=args,
                client=client,
                grader=grader,
                course_id=spec.course_id,
                assignment_id=spec.assignment_id,
                model_override=spec.model,
            )

        result = batch_grader.process_assignment_file(
            args.assignment_file,
            grade_callback,
            skip_disabled=True,
        )

        print(f"\n{'='*60}")
        print("BATCH SUMMARY")
        print(f"{'='*60}")
        print(f"Total: {result.total}")
        print(f"Succeeded: {result.succeeded}")
        print(f"Failed: {result.failed}")

        if result.failures:
            print("\nFailures:")
            for course_id, assignment_id, msg in result.failures:
                print(f"  - course_id={course_id} assignment_id={assignment_id}: {msg}")
            return 1

        return 0

    # Single-assignment mode
    if args.course_id is None or args.assignment_id is None:
        raise RuntimeError("Missing --course-id/--assignment-id (or use --assignment-file for batch mode)")

    return grade_one_assignment(
        args=args,
        client=client,
        grader=grader,
        course_id=args.course_id,
        assignment_id=args.assignment_id,
        model_override=None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
