#!/usr/bin/env python3
"""
Canvas Rubric Reset (Archive old -> Create new -> Optional attach -> Verify)

This version handles the two common Canvas realities you’re hitting:
1) DELETE often 500s -> we "archive" by renaming instead.
2) A rubric can exist but not be visible where you’re looking unless it's ATTACHED to an assignment.

What it does:
- Finds matching rubrics by title
- Tries DELETE; if fails, renames (archives) so the title is freed
- Creates a fresh rubric with the original title
- OPTIONAL: attaches the newly created rubric to an assignment (recommended)
- Verifies both rubric creation and (if requested) assignment association

ENV VARS (or CLI args):
  CANVAS_BASE_URL
  CANVAS_TOKEN
  COURSE_ID
  RUBRIC_TITLE
  ASSIGNMENT_ID              (optional)
  DELETE_MATCH_MODE          exact|contains
  DELETE_MODE                delete|archive|delete_then_archive
  ARCHIVE_SUFFIX             default " (ARCHIVED)"
  DRY_RUN                    1|true
  REQUEST_TIMEOUT            default 30
  DEBUG                      1|true

Usage:
  python reset_rubric.py --base-url https://sussexccc.instructure.com --token $CANVAS_TOKEN --course-id 16388 \
    --rubric-title "Comp I Short Story Rubric (AI)" --assignment-id 123456 --debug
"""

import argparse
import os
import sys
import textwrap
import time
import requests
from typing import Dict, Any, List, Optional, Tuple


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def bool_env(name: str, default: bool = False) -> bool:
    v = env(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "canvas-rubric-reset/4.0",
    }


def now_compact() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def parse_link_header(link: str) -> Dict[str, str]:
    links: Dict[str, str] = {}
    if not link:
        return links
    for part in link.split(","):
        section = part.split(";")
        if len(section) < 2:
            continue
        url = section[0].strip()
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1]
        rel = None
        for seg in section[1:]:
            seg = seg.strip()
            if seg.startswith("rel="):
                rel = seg.split("=", 1)[1].strip().strip('"')
                break
        if rel:
            links[rel] = url
    return links


def get_all(session: requests.Session, url: str, timeout: int) -> List[Any]:
    results: List[Any] = []
    while url:
        r = session.get(url, timeout=timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"GET failed {r.status_code}: {r.text}")
        data = r.json()
        if not isinstance(data, list):
            raise RuntimeError(f"Expected list JSON, got {type(data)}: {data}")
        results.extend(data)
        links = parse_link_header(r.headers.get("Link", ""))
        url = links.get("next")
    return results


def list_rubrics(session: requests.Session, base_url: str, course_id: str, timeout: int) -> List[Dict[str, Any]]:
    return get_all(session, f"{base_url}/api/v1/courses/{course_id}/rubrics?per_page=100", timeout)


def title_matches(actual: str, target: str, mode: str) -> bool:
    a = (actual or "").strip()
    t = (target or "").strip()
    if mode == "contains":
        return t.lower() in a.lower()
    return a == t


def delete_rubric(session: requests.Session, base_url: str, course_id: str, rubric_id: int, timeout: int) -> Tuple[bool, str]:
    url = f"{base_url}/api/v1/courses/{course_id}/rubrics/{rubric_id}"
    r = session.delete(url, timeout=timeout)
    if r.status_code < 400:
        return True, f"Deleted rubric {rubric_id}"
    return False, f"DELETE failed {r.status_code}: {r.text}"


def rename_rubric(session: requests.Session, base_url: str, course_id: str, rubric_id: int,
                  new_title: str, timeout: int) -> Tuple[bool, str]:
    url = f"{base_url}/api/v1/courses/{course_id}/rubrics/{rubric_id}"
    r = session.put(url, data={"rubric[title]": new_title}, timeout=timeout)
    if r.status_code < 400:
        return True, f"Renamed rubric {rubric_id} -> {new_title!r}"
    return False, f"RENAME failed {r.status_code}: {r.text}"


def build_rubric_payload(title: str) -> Dict[str, str]:
    payload: Dict[str, str] = {
        "rubric[title]": title,
        "rubric[free_form_criterion_comments]": "1",
        "rubric[hide_score_total]": "0",
    }

    criteria = [
        ("Content & Ideas", 30, """\
            Evaluate clarity, development, and significance of the story’s central ideas.
            High: clear theme; coherent, developed plot; thoughtful/original ideas.
            Medium: theme present but uneven; plot mostly clear; limited depth.
            Low: theme unclear/missing; plot confusing/incomplete; ideas undeveloped.
        """),
        ("Organization & Structure", 20, """\
            Evaluate story structure and flow.
            High: clear beginning/middle/end; logical progression; smooth transitions.
            Medium: structure apparent but uneven; pacing/transitions issues.
            Low: disorganized; weak or missing structural elements.
        """),
        ("Characterization & Setting", 20, """\
            Evaluate characters and setting.
            High: believable, developed protagonist; setting supports mood/conflict/meaning.
            Medium: present but thinly developed; limited connection to purpose.
            Low: flat/confusing characters; vague/irrelevant setting.
        """),
        ("Style, Tone, & Voice", 15, """\
            Evaluate writing style effectiveness.
            High: consistent, appropriate tone; strong word choice/imagery; clear voice.
            Medium: tone/voice inconsistent; clear but unremarkable language.
            Low: confusing tone; distracting/weak language.
        """),
        ("Grammar, Mechanics, & Formatting", 15, """\
            Evaluate sentence-level correctness and presentation.
            High: few errors; meaning remains clear.
            Medium: repeated errors that sometimes distract.
            Low: frequent errors interfere with understanding.
            Focus on patterns, not isolated mistakes.
        """),
    ]

    for i, (name, points, desc) in enumerate(criteria):
        base = f"rubric[criteria][{i}]"
        payload[f"{base}[description]"] = name
        payload[f"{base}[long_description]"] = textwrap.dedent(desc).strip()
        payload[f"{base}[points]"] = str(points)
        payload[f"{base}[criterion_use_range]"] = "1"
        payload[f"{base}[ratings][0][description]"] = "Full Credit"
        payload[f"{base}[ratings][0][points]"] = str(points)
        payload[f"{base}[ratings][1][description]"] = "No Credit"
        payload[f"{base}[ratings][1][points]"] = "0"

    return payload


def create_rubric(session: requests.Session, base_url: str, course_id: str, title: str,
                  timeout: int, debug: bool) -> Dict[str, Any]:
    url = f"{base_url}/api/v1/courses/{course_id}/rubrics"
    payload = build_rubric_payload(title)

    if debug:
        print("\n--- DEBUG PAYLOAD (preview) ---")
        for k in sorted(payload.keys())[:60]:
            v = payload[k]
            if isinstance(v, str) and len(v) > 120:
                v = v[:120] + "…"
            print(f"{k} = {v!r}")
        print(f"Total payload keys: {len(payload)}")
        print("--- END DEBUG PAYLOAD ---\n")

    r = session.post(url, data=payload, timeout=timeout)
    if debug:
        print(f"POST {url} -> HTTP {r.status_code}")
        print(f"Response Content-Type: {r.headers.get('Content-Type', '')}")

    if r.status_code >= 400:
        raise RuntimeError(f"POST create rubric failed {r.status_code}: {r.text}")

    data = r.json()
    if isinstance(data, dict) and "rubric" in data and isinstance(data["rubric"], dict):
        return data["rubric"]
    if isinstance(data, dict):
        return data
    raise RuntimeError(f"Unexpected JSON response: {data!r}")


def attach_rubric_to_assignment(session: requests.Session, base_url: str, course_id: str,
                                assignment_id: str, rubric_id: int, timeout: int) -> Dict[str, Any]:
    """
    Create a rubric association explicitly. This usually makes the rubric visible
    from the assignment page and in SpeedGrader.

    Endpoint: POST /courses/:course_id/rubric_associations
    """
    url = f"{base_url}/api/v1/courses/{course_id}/rubric_associations"
    payload = {
        "rubric_association[association_type]": "Assignment",
        "rubric_association[association_id]": str(assignment_id),
        "rubric_association[rubric_id]": str(rubric_id),
        "rubric_association[purpose]": "grading",
        "rubric_association[use_for_grading]": "1",
    }
    r = session.post(url, data=payload, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"POST rubric association failed {r.status_code}: {r.text}")
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive/delete and recreate a Canvas rubric; optionally attach to assignment.")
    ap.add_argument("--base-url", default=env("CANVAS_BASE_URL"))
    ap.add_argument("--token", default=env("CANVAS_TOKEN"))
    ap.add_argument("--course-id", default=env("COURSE_ID"))
    ap.add_argument("--rubric-title", default=env("RUBRIC_TITLE", "Comp I Short Story Rubric (AI)"))
    ap.add_argument("--assignment-id", default=env("ASSIGNMENT_ID"))
    ap.add_argument("--delete-match-mode", default=env("DELETE_MATCH_MODE", "exact"), choices=["exact", "contains"])
    ap.add_argument("--delete-mode", default=env("DELETE_MODE", "delete_then_archive"),
                    choices=["delete", "archive", "delete_then_archive"])
    ap.add_argument("--archive-suffix", default=env("ARCHIVE_SUFFIX", " (ARCHIVED)"))
    ap.add_argument("--dry-run", action="store_true", default=bool_env("DRY_RUN", False))
    ap.add_argument("--debug", action="store_true", default=bool_env("DEBUG", False))
    ap.add_argument("--timeout", type=int, default=int(env("REQUEST_TIMEOUT", "30")))
    args = ap.parse_args()

    missing = []
    if not args.base_url:
        missing.append("CANVAS_BASE_URL/--base-url")
    if not args.token:
        missing.append("CANVAS_TOKEN/--token")
    if not args.course_id:
        missing.append("COURSE_ID/--course-id")
    if missing:
        print("ERROR: Missing required configuration:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 2

    base_url = normalize_base_url(args.base_url)
    course_id = str(args.course_id)
    rubric_title = args.rubric_title.strip()
    timeout = args.timeout

    session = requests.Session()
    session.headers.update(headers(args.token))

    rubrics = list_rubrics(session, base_url, course_id, timeout)
    matches = [r for r in rubrics if title_matches(r.get("title", ""), rubric_title, args.delete_match_mode)]

    print(f"Found {len(matches)} matching rubric(s) ({args.delete_match_mode}) for title: {rubric_title!r}")
    for r in matches:
        print(f"  - id={r.get('id')} title={r.get('title')!r}")

    if args.dry_run:
        print("\nDRY RUN — no changes made.")
        return 0

    # Handle existing
    for r in matches:
        rid = r.get("id")
        if rid is None:
            continue
        rid = int(rid)

        if args.delete_mode in ("delete", "delete_then_archive"):
            ok, msg = delete_rubric(session, base_url, course_id, rid, timeout)
            if ok:
                print(msg)
                continue
            print(f"WARNING: {msg}")
            if args.delete_mode == "delete":
                print("ERROR: delete-mode=delete and deletion failed. Aborting.", file=sys.stderr)
                return 1

        if args.delete_mode in ("archive", "delete_then_archive"):
            new_title = f"{rubric_title}{args.archive_suffix} {now_compact()} [id {rid}]"
            ok, msg = rename_rubric(session, base_url, course_id, rid, new_title, timeout)
            if not ok:
                print(f"ERROR: {msg}", file=sys.stderr)
                return 1
            print(msg)

    # Create fresh
    created = create_rubric(session, base_url, course_id, rubric_title, timeout, debug=args.debug)
    created_id = created.get("id")
    created_title = created.get("title")

    if created_id is None:
        print(f"ERROR: Create returned no rubric id. Response: {created}", file=sys.stderr)
        return 1

    print(f"\nCreated rubric: id={created_id} title={created_title!r}")

    # Verify rubric exists by re-list
    rubrics_after = list_rubrics(session, base_url, course_id, timeout)
    exact_after = [rr for rr in rubrics_after if (rr.get("title") or "").strip() == rubric_title]
    if not exact_after:
        print("ERROR: Rubric not found after re-listing; likely permissions/UI visibility issue.", file=sys.stderr)
        return 1

    # Optional attach
    if args.assignment_id:
        assoc = attach_rubric_to_assignment(
            session, base_url, course_id, str(args.assignment_id), int(created_id), timeout
        )
        assoc_id = assoc.get("id")
        print(f"Attached rubric to assignment {args.assignment_id}. Association id={assoc_id}")

    print("\nSUCCESS: Rubric exists in Canvas via API.")
    print("If you still can’t see it in the UI, use: Assignment → +Rubric → Find a Rubric (search by title).")
    print("If you cannot access Course Settings → Rubrics, it’s a role/permissions/navigation issue, not creation.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
