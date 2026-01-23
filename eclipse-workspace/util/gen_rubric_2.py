#!/usr/bin/env python3
"""
Canvas Rubric Upsert — Rhetorical Analysis Essay (with Formatting)

Behavior:
- If a rubric with RUBRIC_TITLE exists in the course:
    - Update it in place (PUT /rubrics/:id) with current criteria payload
  Else:
    - Create it (POST /rubrics)

- Ensure the rubric is attached to ASSIGNMENT_ID (default 368044):
    - Detect current attachment by fetching the assignment with include[]=rubric_association
      (this avoids /rubric_associations listing, which is 404 in your Canvas instance)
    - If already attached to the same rubric_id: done
    - Otherwise: POST a new rubric association to attach. If Canvas rejects it, we fail loudly
      so you can detach/replace in the UI (or revert to archive+create strategy).

Note on "clean strategy":
- Updating rubrics in place is clean and avoids rubric clutter, but Canvas behavior around
  updating rubric criteria can vary by instance. If your instance fails to update criteria
  reliably, revert to the archive+create pattern you used in gen_rubric_1.py.
"""

import argparse
import os
import sys
import textwrap
import requests
from typing import Dict, Any, List, Optional


# -----------------------------
# Utility helpers
# -----------------------------

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
        "User-Agent": "canvas-rubric-upsert/1.1",
    }


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
            raise RuntimeError(f"Expected list JSON, got {type(data)}")
        results.extend(data)
        links = parse_link_header(r.headers.get("Link", ""))
        url = links.get("next")
    return results


def list_rubrics(session: requests.Session, base_url: str, course_id: str, timeout: int) -> List[Dict[str, Any]]:
    return get_all(session, f"{base_url}/api/v1/courses/{course_id}/rubrics?per_page=100", timeout)


def find_rubric_by_exact_title(
    rubrics: List[Dict[str, Any]],
    title: str,
) -> Optional[Dict[str, Any]]:
    t = (title or "").strip()
    for r in rubrics:
        if not isinstance(r, dict):
            continue
        if (r.get("title") or "").strip() == t:
            return r
    return None


# -----------------------------
# Rubric payload (includes Formatting)
# -----------------------------

def build_rubric_payload(title: str) -> Dict[str, str]:
    """
    Total = 100 points.
    Includes a Formatting criterion explicitly marked instructor-graded.
    """
    payload: Dict[str, str] = {
        "rubric[title]": title,
        "rubric[free_form_criterion_comments]": "1",
        "rubric[hide_score_total]": "0",
    }

    criteria = [
        (
            "Rhetorical Understanding & Thesis",
            30,
            """\
            Evaluate the clarity of the thesis and the student’s understanding of the author’s purpose,
            audience, and rhetorical situation.

            High: Clear, focused thesis; strong understanding of rhetorical context.
            Medium: Thesis present but general or uneven; rhetorical context partially addressed.
            Low: Thesis unclear or missing; weak rhetorical understanding.
            """
        ),
        (
            "Use of Evidence & Analysis",
            25,
            """\
            Evaluate the selection, integration, and explanation of textual evidence.

            High: Evidence is well-chosen, integrated, and thoughtfully analyzed (analysis, not summary).
            Medium: Evidence present but sometimes underexplained or loosely connected to claims.
            Low: Minimal evidence or primarily summary with limited analysis.
            """
        ),
        (
            "Organization & Coherence",
            20,
            """\
            Evaluate logical organization, paragraph focus, and flow of ideas.

            High: Clear analytical structure with effective transitions and focused paragraphs.
            Medium: Organization apparent but uneven; some paragraph or transition issues.
            Low: Disorganized or difficult to follow.
            """
        ),
        (
            "Style, Tone, & Academic Voice",
            10,
            """\
            Evaluate clarity, tone, and appropriateness of academic voice.

            High: Consistent academic tone; precise, effective language.
            Medium: Generally clear but occasionally informal, vague, or repetitive.
            Low: Tone inappropriate or language interferes with clarity.
            """
        ),
        (
            "Grammar & Mechanics",
            5,
            """\
            Evaluate sentence-level correctness.

            High: Few errors; meaning always clear.
            Medium: Repeated errors that sometimes distract but do not obscure meaning.
            Low: Frequent errors that interfere with understanding.

            Focus on patterns, not isolated mistakes.
            """
        ),
        (
            "Formatting & MLA Presentation",
            10,
            """\
            Evaluate adherence to MLA formatting guidelines, including document layout, heading/header,
            spacing, and citation format.

            IMPORTANT: This criterion is graded by the instructor (not the AI grader).
            The AI grader may acknowledge formatting expectations but does not assign points for this category.
            """
        ),
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


# -----------------------------
# API operations
# -----------------------------

def create_rubric(
    session: requests.Session,
    base_url: str,
    course_id: str,
    payload: Dict[str, str],
    timeout: int
) -> Dict[str, Any]:
    url = f"{base_url}/api/v1/courses/{course_id}/rubrics"
    r = session.post(url, data=payload, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"Create rubric failed {r.status_code}: {r.text}")
    data = r.json()
    if isinstance(data, dict) and isinstance(data.get("rubric"), dict):
        return data["rubric"]
    if isinstance(data, dict):
        return data
    raise RuntimeError(f"Unexpected create rubric response: {data!r}")


def update_rubric(
    session: requests.Session,
    base_url: str,
    course_id: str,
    rubric_id: int,
    payload: Dict[str, str],
    timeout: int
) -> Dict[str, Any]:
    """
    Attempts in-place update of rubric title/settings/criteria.
    Canvas support for updating criteria can vary; if this fails in your instance,
    revert to archive+create.
    """
    url = f"{base_url}/api/v1/courses/{course_id}/rubrics/{rubric_id}"
    r = session.put(url, data=payload, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"Update rubric failed {r.status_code}: {r.text}")
    data = r.json()
    if isinstance(data, dict) and isinstance(data.get("rubric"), dict):
        return data["rubric"]
    if isinstance(data, dict):
        return data
    raise RuntimeError(f"Unexpected update rubric response: {data!r}")


def get_assignment_rubric_association(
    session: requests.Session,
    base_url: str,
    course_id: str,
    assignment_id: str,
    timeout: int
) -> Optional[Dict[str, Any]]:
    """
    Your Canvas instance returns 404 for GET /courses/:id/rubric_associations?... (listing).
    This is a reliable alternative: fetch the assignment and read rubric_association.
    """
    url = (
        f"{base_url}/api/v1/courses/{course_id}/assignments/{assignment_id}"
        f"?include[]=rubric_association&include[]=rubric&include[]=rubric_settings"
    )
    r = session.get(url, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"GET assignment failed {r.status_code}: {r.text}")

    a = r.json()
    ra = a.get("rubric_association")
    return ra if isinstance(ra, dict) else None


def attach_rubric_to_assignment(
    session: requests.Session,
    base_url: str,
    course_id: str,
    assignment_id: str,
    rubric_id: int,
    timeout: int
) -> Dict[str, Any]:
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
        raise RuntimeError(f"Attach rubric failed {r.status_code}: {r.text}")
    return r.json()


# -----------------------------
# Main
# -----------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Upsert rubric (update if exists, else create) and attach to assignment.")
    ap.add_argument("--base-url", default=env("CANVAS_BASE_URL"))
    ap.add_argument("--token", default=env("CANVAS_TOKEN"))
    ap.add_argument("--course-id", default=env("COURSE_ID"))
    ap.add_argument("--assignment-id", default=env("ASSIGNMENT_ID", "368044"))
    ap.add_argument("--rubric-title", default=env("RUBRIC_TITLE", "Comp I Rhetorical Analysis Rubric (AI + Format)"))
    ap.add_argument("--timeout", type=int, default=int(env("REQUEST_TIMEOUT", "30")))
    ap.add_argument("--debug", action="store_true", default=bool_env("DEBUG", False))
    args = ap.parse_args()

    if not args.base_url or not args.token or not args.course_id:
        print("Missing required Canvas configuration.", file=sys.stderr)
        return 2

    base_url = normalize_base_url(args.base_url)
    course_id = str(args.course_id)
    assignment_id = str(args.assignment_id).strip()
    rubric_title = str(args.rubric_title).strip()
    timeout = int(args.timeout)

    session = requests.Session()
    session.headers.update(headers(args.token))

    payload = build_rubric_payload(rubric_title)

    if args.debug:
        print("\n--- DEBUG PAYLOAD KEYS (count) ---")
        print(f"Total payload keys: {len(payload)}")
        print("--- END DEBUG ---\n")

    rubrics = list_rubrics(session, base_url, course_id, timeout)
    existing = find_rubric_by_exact_title(rubrics, rubric_title)

    rubric_obj: Dict[str, Any]
    if existing and isinstance(existing.get("id"), int):
        rid = int(existing["id"])
        print(f"Found existing rubric with exact title: id={rid} title={rubric_title!r}")
        try:
            rubric_obj = update_rubric(session, base_url, course_id, rid, payload, timeout)
            print(f"Updated rubric in place: id={rid}")
        except Exception as e:
            # If update fails, create a new rubric (no rename/archive here).
            print(f"WARNING: Update failed; creating new rubric instead. Reason: {e}", file=sys.stderr)
            rubric_obj = create_rubric(session, base_url, course_id, payload, timeout)
            print(f"Created rubric: id={rubric_obj.get('id')}")
    else:
        rubric_obj = create_rubric(session, base_url, course_id, payload, timeout)
        print(f"Created rubric: id={rubric_obj.get('id')} title={rubric_title!r}")

    rubric_id = rubric_obj.get("id")
    if rubric_id is None:
        print("ERROR: rubric id missing from response; cannot attach.", file=sys.stderr)
        return 1

    # Ensure association (without calling the listing endpoint that 404s in your instance)
    if assignment_id:
        ra = get_assignment_rubric_association(session, base_url, course_id, assignment_id, timeout)

        if ra and str(ra.get("rubric_id")) == str(rubric_id):
            print(f"Rubric already attached to assignment {assignment_id} (association id={ra.get('id')}).")
        else:
            # Attach rubric. If Canvas rejects due to existing rubric association rules,
            # fail loudly so you can detach/replace via UI or switch to archive+create.
            attach_rubric_to_assignment(session, base_url, course_id, assignment_id, int(rubric_id), timeout)
            print(f"Attached rubric to assignment {assignment_id}.")

    print("SUCCESS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
