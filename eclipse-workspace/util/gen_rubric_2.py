#!/usr/bin/env python3
"""
Canvas Rubric Reset — Rhetorical Analysis Essay

Based on gen_rubric_1.py (Short Story), with criteria rewritten
for a rhetorical analysis / expository assignment.

Recommended for assignment_id = 368044
"""

import argparse
import os
import sys
import textwrap
import time
import requests
from typing import Dict, Any, List, Optional, Tuple


# -----------------------------
# Utility helpers (unchanged)
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
            raise RuntimeError(f"Expected list JSON, got {type(data)}")
        results.extend(data)
        links = parse_link_header(r.headers.get("Link", ""))
        url = links.get("next")
    return results


# -----------------------------
# Rubric logic
# -----------------------------

def build_rubric_payload(title: str) -> Dict[str, str]:
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
            Evaluate the student’s understanding of the author’s purpose,
            audience, and rhetorical situation, as well as the clarity of the
            central analytical claim (thesis).

            High: Clear, focused thesis that identifies purpose and audience;
            analysis demonstrates strong understanding of rhetorical context.

            Medium: Thesis present but general or uneven; rhetorical context
            partially addressed.

            Low: Thesis unclear or missing; little understanding of rhetorical
            purpose or audience.
            """
        ),
        (
            "Use of Evidence & Analysis",
            25,
            """\
            Evaluate the use of textual evidence and the quality of explanation
            connecting evidence to claims.

            High: Well-chosen quotations or examples integrated smoothly and
            analyzed thoughtfully.

            Medium: Evidence present but sometimes underexplained or loosely
            connected to claims.

            Low: Minimal, poorly chosen, or unexplained evidence; summary
            replaces analysis.
            """
        ),
        (
            "Organization & Coherence",
            20,
            """\
            Evaluate logical organization, paragraph structure, and flow of ideas.

            High: Clear analytical structure with focused paragraphs and smooth
            transitions.

            Medium: Overall organization apparent but uneven; some paragraph or
            transition issues.

            Low: Disorganized or difficult to follow; weak paragraph focus.
            """
        ),
        (
            "Style, Tone, & Academic Voice",
            15,
            """\
            Evaluate clarity, tone, and appropriateness of academic voice.

            High: Consistent academic tone; clear, precise language.

            Medium: Generally clear but occasionally informal, vague, or repetitive.

            Low: Tone inappropriate or inconsistent; language interferes with clarity.
            """
        ),
        (
            "Grammar & Mechanics",
            10,
            """\
            Evaluate sentence-level correctness.

            High: Few errors; meaning always clear.

            Medium: Repeated errors that sometimes distract but do not obscure meaning.

            Low: Frequent errors that interfere with understanding.

            Focus on patterns, not isolated mistakes.
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
# Main
# -----------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Create or reset rubric for Rhetorical Analysis assignment.")
    ap.add_argument("--base-url", default=env("CANVAS_BASE_URL"))
    ap.add_argument("--token", default=env("CANVAS_TOKEN"))
    ap.add_argument("--course-id", default=env("COURSE_ID"))
    ap.add_argument("--assignment-id", default=env("ASSIGNMENT_ID", "368044"))
    ap.add_argument("--rubric-title", default=env("RUBRIC_TITLE", "Comp I Rhetorical Analysis Rubric (AI)"))
    ap.add_argument("--timeout", type=int, default=int(env("REQUEST_TIMEOUT", "30")))
    ap.add_argument("--debug", action="store_true", default=bool_env("DEBUG", False))
    args = ap.parse_args()

    if not args.base_url or not args.token or not args.course_id:
        print("Missing required Canvas configuration.", file=sys.stderr)
        return 2

    base_url = normalize_base_url(args.base_url)
    session = requests.Session()
    session.headers.update(headers(args.token))

    payload = build_rubric_payload(args.rubric_title)

    url = f"{base_url}/api/v1/courses/{args.course_id}/rubrics"
    r = session.post(url, data=payload, timeout=args.timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"Create rubric failed {r.status_code}: {r.text}")

    rubric = r.json().get("rubric", {})
    rubric_id = rubric.get("id")

    print(f"Created rubric: {args.rubric_title} (id={rubric_id})")

    if args.assignment_id and rubric_id:
        assoc_url = f"{base_url}/api/v1/courses/{args.course_id}/rubric_associations"
        assoc_payload = {
            "rubric_association[association_type]": "Assignment",
            "rubric_association[association_id]": str(args.assignment_id),
            "rubric_association[rubric_id]": str(rubric_id),
            "rubric_association[purpose]": "grading",
            "rubric_association[use_for_grading]": "1",
        }
        ar = session.post(assoc_url, data=assoc_payload, timeout=args.timeout)
        if ar.status_code >= 400:
            raise RuntimeError(f"Attach rubric failed {ar.status_code}: {ar.text}")
        print(f"Attached rubric to assignment {args.assignment_id}")

    print("SUCCESS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
