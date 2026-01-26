import argparse
import csv
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
    attempt = submission.get("attempt")
    submitted_at = submission.get("submitted_at") or submission.get("posted_at") or ""
    updated_at = submission.get("updated_at") or ""
    body = submission.get("body") or ""
    if not isinstance(body, str):
        body = ""
    body_hash = _sha256_text(body)

    if attempt is None:
        return f"attempt=?|submitted_at={submitted_at}|updated_at={updated_at}|body_sha256={body_hash}"
    return f"attempt={attempt}|submitted_at={submitted_at}|updated_at={updated_at}|body_sha256={body_hash}"


def already_assessed(submission_with_comments: dict, fp: str) -> bool:
    comments = submission_with_comments.get("submission_comments") or []
    if not isinstance(comments, list):
        return False

    needle = f"{FINGERPRINT_PREFIX} {fp}"
    for c in comments:
        if not isinstance(c, dict):
            continue
        txt = c.get("comment") or ""
        if isinstance(txt, str) and needle in txt:
            return True
    return False


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AIGrader test driver (prompt files + idempotency).")

    p.add_argument("--base-url", default=None)
    p.add_argument("--token", default=None)

    # Single-assignment mode
    p.add_argument("--course-id", type=int, required=False)
    p.add_argument("--assignment-id", type=int, required=False)

    # Multi-assignment mode: tab-delimited or CSV with header
    p.add_argument(
        "--assignment-file",
        default=None,
        help="Path to a TSV/CSV file with columns: course_id, assignment_id, enabled, model, notes",
    )

    p.add_argument("--user-id", type=int, default=None)

    p.add_argument("--use-llm", action="store_true")
    p.add_argument("--openai-model", default=None)
    p.add_argument("--openai-key", default=None)
    p.add_argument("--temperature", type=float, default=None)
    p.add_argument("--reasoning-effort", default=None)

    p.add_argument("--post-comment", action="store_true")
    p.add_argument("--comment-html", action="store_true")

    p.add_argument("--print-prompts", action="store_true")
    p.add_argument("--save-raw", default=None)

    return p.parse_args()


def _join_system_prompts(initial_prompt: str, assignment_prompt: str | None) -> str:
    if assignment_prompt and assignment_prompt.strip():
        return initial_prompt.strip() + "\n\n" + assignment_prompt.strip()
    return initial_prompt.strip()


def _truthy(v: object) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in {"1", "true", "t", "yes", "y", "on"}


def iter_assignment_rows(path: str) -> list[dict]:
    """Read a TSV/CSV assignment list.

    Expected header columns (case-insensitive):
      - course_id
      - assignment_id
      - enabled
      - model
      - notes
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)

        # Default to TSV if we see tabs in the header/sample; otherwise CSV.
        delimiter = "\t" if "\t" in sample.splitlines()[0] else ","
        rdr = csv.DictReader(f, delimiter=delimiter)

        rows: list[dict] = []
        for i, row in enumerate(rdr, start=2):  # header is line 1
            if not row:
                continue
            # normalize keys to lower
            row_norm = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}

            try:
                course_id = int(row_norm.get("course_id") or 0)
                assignment_id = int(row_norm.get("assignment_id") or 0)
            except Exception as e:
                raise ValueError(f"Invalid course_id/assignment_id at line {i}: {row}") from e

            if course_id <= 0 or assignment_id <= 0:
                raise ValueError(f"Missing/invalid course_id or assignment_id at line {i}: {row}")

            enabled = _truthy(row_norm.get("enabled", "true"))
            model = (row_norm.get("model") or "").strip() or None
            notes = (row_norm.get("notes") or "").strip()

            rows.append(
                {
                    "course_id": course_id,
                    "assignment_id": assignment_id,
                    "enabled": enabled,
                    "model": model,
                    "notes": notes,
                }
            )
        return rows


def grade_one_assignment(
    *,
    args: argparse.Namespace,
    client: CanvasClient,
    grader: AIGrader,
    course_id: int,
    assignment_id: int,
    model_override: str | None,
) -> int:
    """Run the existing single-assignment pipeline once. Returns 0 on success, 1 on failure."""
    run = grader.grade_assignment(
        course_id=course_id,
        assignment_id=assignment_id,
        user_id=args.user_id,
    )

    print("\n=== PREFLIGHT SUMMARY ===")
    print(run.preflight)

    # -----------------------------
    # Load prompt files (system prompt)
    # -----------------------------
    initial_prompt = client.get_course_file_text(
        course_id=course_id,
        folder_path="AIGrader",
        filename="initial_prompt.txt",
    )

    assignment_prompt_filename = f"assignment_{assignment_id}_prompt.txt"
    assignment_prompt = None
    try:
        assignment_prompt = client.get_course_file_text(
            course_id=course_id,
            folder_path="AIGrader",
            filename=assignment_prompt_filename,
        )
    except FileNotFoundError:
        assignment_prompt = None

    system_prompt = _join_system_prompts(initial_prompt, assignment_prompt)

    if assignment_prompt is None:
        print("\nSystem prompt source: AIGrader/initial_prompt.txt")
        print(f"Assignment prompt: (none) AIGrader/{assignment_prompt_filename}")
    else:
        print("\nSystem prompt source: AIGrader/initial_prompt.txt + " f"AIGrader/{assignment_prompt_filename}")

    # -----------------------------
    # Idempotency: skip if already assessed for current submission state
    # -----------------------------
    sub = client.get_submission_with_comments(
        course_id=course_id,
        assignment_id=assignment_id,
        user_id=run.preflight.submission_user_id,
    )
    fp = compute_submission_fingerprint(sub)

    if already_assessed(sub, fp):
        print("\nSKIP: Existing AIGrader assessment already posted for this submission (no resubmission detected).")
        print(f"{FINGERPRINT_PREFIX} {fp}")
        return 0

    # Build prompts
    spec = build_prompts(run, system_prompt=system_prompt)

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

    # Call LLM (or mock)
    if args.use_llm:
        if LLMClient is None:
            raise RuntimeError("Could not import aigrader.llm.LLMClient.")

        chosen_model = model_override or args.openai_model
        llm = LLMClient(api_key=args.openai_key, model=chosen_model)
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
            # If running multi-assignment, avoid collisions by suffixing with assignment id
            save_path = args.save_raw
            root, ext = os.path.splitext(save_path)
            if root and ext:
                save_path = f"{root}_{course_id}_{assignment_id}{ext}"
            elif root:
                save_path = f"{root}_{course_id}_{assignment_id}.txt"

            with open(save_path, "w", encoding="utf-8") as f:
                f.write(raw_text)
            print(f"Saved raw model output to: {save_path}")

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

    # Post comment (stamp fingerprint so we can skip next time)
    if args.post_comment:
        if args.comment_html:
            comment = render_ai_assessment_comment_html(run, result, meta=meta)
            comment = comment + f"<p><em>{FINGERPRINT_PREFIX} {fp}</em></p>"
            client.add_submission_comment(
                course_id=course_id,
                assignment_id=assignment_id,
                user_id=run.preflight.submission_user_id,
                text_comment=comment,
                as_html=True,
            )
            print("\nPosted AI assessment as a Canvas HTML comment (Not Applied).")
        else:
            comment = render_ai_assessment_comment(run, result, meta=meta)
            comment = comment + "\n\n" + f"{FINGERPRINT_PREFIX} {fp}"
            client.add_submission_comment(
                course_id=course_id,
                assignment_id=assignment_id,
                user_id=run.preflight.submission_user_id,
                text_comment=comment,
                as_html=False,
            )
            print("\nPosted AI assessment as a Canvas text comment (Not Applied).")

    print("\nOK: End-to-end prompt + validation pipeline succeeded.")
    print(f"{FINGERPRINT_PREFIX} {fp}")
    return 0


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

    # Multi-assignment mode
    if args.assignment_file:
        rows = iter_assignment_rows(args.assignment_file)
        enabled_rows = [r for r in rows if r["enabled"]]

        print(f"Loaded {len(rows)} rows from {args.assignment_file}; enabled={len(enabled_rows)}")

        failures: list[tuple[int, int, str]] = []
        for idx, r in enumerate(enabled_rows, start=1):
            course_id = int(r["course_id"])
            assignment_id = int(r["assignment_id"])
            model = r.get("model")
            notes = r.get("notes") or ""

            banner = f"[{idx}/{len(enabled_rows)}] course_id={course_id} assignment_id={assignment_id}"
            if model:
                banner += f" model={model}"
            if notes:
                banner += f" notes={notes}"
            print("\n" + "=" * len(banner))
            print(banner)
            print("=" * len(banner))

            try:
                rc = grade_one_assignment(
                    args=args,
                    client=client,
                    grader=grader,
                    course_id=course_id,
                    assignment_id=assignment_id,
                    model_override=model,
                )
                if rc != 0:
                    failures.append((course_id, assignment_id, "nonzero return"))
            except Exception as e:
                failures.append((course_id, assignment_id, f"{type(e).__name__}: {e}"))
                print(f"ERROR grading course_id={course_id} assignment_id={assignment_id}: {e}")

        if failures:
            print("\n=== SUMMARY: FAILURES ===")
            for course_id, assignment_id, msg in failures:
                print(f"- course_id={course_id} assignment_id={assignment_id}: {msg}")
            return 1

        print("\n=== SUMMARY ===")
        print("All enabled assignments processed successfully.")
        return 0

    # Single-assignment mode
    if args.course_id is None or args.assignment_id is None:
        raise RuntimeError("Missing --course-id/--assignment-id (or provide --assignment-file).")

    return grade_one_assignment(
        args=args,
        client=client,
        grader=grader,
        course_id=int(args.course_id),
        assignment_id=int(args.assignment_id),
        model_override=None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
