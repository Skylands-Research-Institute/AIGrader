import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
WORKSPACE = HERE.parents[1]
AIGRADER_SRC = WORKSPACE / "aigrader" / "src"
if AIGRADER_SRC.exists():
    sys.path.insert(0, str(AIGRADER_SRC))

from aigrader.assessment_comment import (
    CommentMetadata,
    render_ai_assessment_comment,
    render_ai_assessment_comment_html,
)
from aigrader.canvas.client import CanvasAuth, CanvasClient
from aigrader.grader import AIGrader
from aigrader.prompt_builder import build_prompts
from aigrader.score_parser import parse_and_validate

try:
    from aigrader.llm import LLMClient
except Exception:
    LLMClient = None  # type: ignore


FINGERPRINT_PREFIX = "aigrader_fingerprint:"


def _sha256_text(s: str) -> str:
    h = hashlib.sha256()
    h.update(s.encode("utf-8"))
    return h.hexdigest()


def compute_submission_fingerprint(submission: dict) -> str:
    """
    Fingerprint changes if the student resubmits.
    Use attempt if present; otherwise submitted_at/updated_at + body hash.
    """
    attempt = submission.get("attempt")
    submitted_at = submission.get("submitted_at") or submission.get("posted_at") or ""
    updated_at = submission.get("updated_at") or ""
    body = submission.get("body") or ""
    if not isinstance(body, str):
        body = ""

    body_hash = _sha256_text(body)

    # attempt is the best discriminator; if missing, rely on timestamps + body hash
    if attempt is None:
        return f"attempt=?|submitted_at={submitted_at}|updated_at={updated_at}|body_sha256={body_hash}"
    return f"attempt={attempt}|submitted_at={submitted_at}|updated_at={updated_at}|body_sha256={body_hash}"


def comment_contains_fingerprint(comment_text: str, fp: str) -> bool:
    if not comment_text:
        return False
    # We require the exact fingerprint line to match
    return f"{FINGERPRINT_PREFIX} {fp}" in comment_text


def already_assessed(submission_with_comments: dict, fp: str) -> bool:
    comments = submission_with_comments.get("submission_comments") or []
    if not isinstance(comments, list):
        return False

    for c in comments:
        if not isinstance(c, dict):
            continue
        # Canvas typically returns "comment" as the text body
        txt = c.get("comment") or ""
        if not isinstance(txt, str):
            continue
        if comment_contains_fingerprint(txt, fp):
            return True
    return False


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AIGrader test driver (LLM + optional Canvas comment + idempotency).")

    p.add_argument("--base-url", default=None)
    p.add_argument("--token", default=None)
    p.add_argument("--course-id", type=int, required=True)
    p.add_argument("--assignment-id", type=int, required=True)
    p.add_argument("--user-id", type=int, default=None)

    p.add_argument("--use-llm", action="store_true")
    p.add_argument("--openai-model", default=None)
    p.add_argument("--temperature", type=float, default=None)
    p.add_argument("--reasoning-effort", default=None)

    p.add_argument("--post-comment", action="store_true")
    p.add_argument("--comment-html", action="store_true")

    p.add_argument("--print-prompts", action="store_true")
    p.add_argument("--save-raw", default=None)

    return p.parse_args()


def main() -> int:
    args = parse_args()

    base_url = (args.base_url or os.getenv("CANVAS_BASE_URL") or "").strip()
    token = (args.token or os.getenv("CANVAS_TOKEN") or "").strip()

    if not base_url:
        raise RuntimeError("Missing --base-url or CANVAS_BASE_URL")
    if not token:
        raise RuntimeError("Missing --token or CANVAS_TOKEN")

    client = CanvasClient(CanvasAuth(base_url=base_url, token=token))
    grader = AIGrader(client)

    run = grader.grade_assignment(
        course_id=args.course_id,
        assignment_id=args.assignment_id,
        user_id=args.user_id,
    )

    print("\n=== PREFLIGHT SUMMARY ===")
    print(run.preflight)

    # --- Idempotency check (skip if same submission already assessed) ---
    sub = client.get_submission_with_comments(
        course_id=args.course_id,
        assignment_id=args.assignment_id,
        user_id=run.preflight.submission_user_id,
    )
    fp = compute_submission_fingerprint(sub)

    if already_assessed(sub, fp):
        print("\nSKIP: This submission already has an AIGrader assessment comment for the current attempt/body.")
        print(f"{FINGERPRINT_PREFIX} {fp}")
        return 0

    # Build prompts
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

    meta = CommentMetadata(model=None, response_id=None)

    # Get model output (real or mock)
    if args.use_llm:
        if LLMClient is None:
            raise RuntimeError("Could not import aigrader.llm.LLMClient.")

        llm = LLMClient(model=args.openai_model)
        print("\n=== CALLING LLM ===")
        resp = llm.generate(
            system_prompt=spec.system_prompt,
            user_prompt=spec.user_prompt,
            reasoning_effort=args.reasoning_effort,
            temperature=args.temperature,
        )
        raw_text = resp.text
        meta = CommentMetadata(model=resp.model, response_id=resp.response_id)

        print(f"LLM response_id={resp.response_id} model={resp.model}")
        if resp.usage:
            print(f"LLM usage={resp.usage}")

        if args.save_raw:
            with open(args.save_raw, "w", encoding="utf-8") as f:
                f.write(raw_text)
            print(f"Saved raw model output to: {args.save_raw}")

    else:
        criteria_obj = {}
        total = 0.0
        for c in run.rubric.criteria:
            criteria_obj[c.id] = {"score": float(c.points), "comment": f"Strong work on {c.description.lower()}."}
            total += float(c.points)

        raw_text = json.dumps(
            {"overall_score": total, "overall_comment": "Mock assessment.", "criteria": criteria_obj},
            indent=2,
            ensure_ascii=False,
        )

    # Parse + validate
    result = parse_and_validate(raw_text, run)

    print("\n=== PARSED ASSESSMENT RESULT ===")
    print(f"Overall score: {result.overall_score:g}")
    print(f"Overall comment: {result.overall_comment}")

    # Post comment (with fingerprint stamp)
    if args.post_comment:
        if args.comment_html:
            comment = render_ai_assessment_comment_html(run, result, meta=meta)
            comment = comment + f"<p><em>{FINGERPRINT_PREFIX} {fp}</em></p>"
            client.add_submission_comment(
                course_id=args.course_id,
                assignment_id=args.assignment_id,
                user_id=run.preflight.submission_user_id,
                text_comment=comment,
                as_html=True,
            )
            print("\nPosted AI assessment as a Canvas HTML comment (Not Applied).")
        else:
            comment = render_ai_assessment_comment(run, result, meta=meta)
            comment = comment + "\n\n" + f"{FINGERPRINT_PREFIX} {fp}"
            client.add_submission_comment(
                course_id=args.course_id,
                assignment_id=args.assignment_id,
                user_id=run.preflight.submission_user_id,
                text_comment=comment,
                as_html=False,
            )
            print("\nPosted AI assessment as a Canvas text comment (Not Applied).")

    print("\nOK: End-to-end prompt + validation pipeline succeeded.")
    print(f"{FINGERPRINT_PREFIX} {fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
