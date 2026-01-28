#!/usr/bin/env python3
"""
gen_rubric_3.py
Create or update the "RUBRIC FOR FORMAL ESSAYS" in Canvas
and optionally attach it to an assignment.

Matches Canvas UI rubric exactly:
- 4 criteria
- 4 rating bands each
- Range scoring enabled
- Total points: 100
"""

import os
import sys
import argparse
import requests


# -----------------------------
# Config helpers
# -----------------------------

def env(name, default=None):
    v = os.getenv(name)
    return v if v not in (None, "") else default


def headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }


# -----------------------------
# Rubric definition
# -----------------------------

RUBRIC_TITLE = "RUBRIC FOR FORMAL ESSAYS"

RUBRIC_CRITERIA = [
    {
        "title": "THESIS / PERSPECTIVE",
        "points": 40,
        "long_description": (
            "This category focuses on the attention of the essay to genre, purpose, "
            "and audience. This category also attends closely to the strength of both "
            "the thesis statement and the larger perspective expanded upon in the essay."
        ),
        "ratings": [
            ("EXCEPTIONAL", 40),
            ("IMPRESSIVE", 30),
            ("EMERGING", 21),
            ("DEVELOPING", 10),
        ],
    },
    {
        "title": "TEXTUAL (OR SUPPORTING) EVIDENCE",
        "points": 30,
        "long_description": (
            "This criterion refers specifically to the use of textual evidence "
            "throughout the essay. Does, for example, the essay responsibly summarize, "
            "paraphrase, and quote from sources included? Are the required number of "
            "sources included and, if so, are they effectively synthesized? For "
            "assignments which do not require the use of outside sources attention "
            "will be given to the use of relevant detail in supporting a perspective."
        ),
        "ratings": [
            ("EXCEPTIONAL", 30),
            ("IMPRESSIVE", 26),
            ("EMERGING", 16),
            ("DEVELOPING", 7),
        ],
    },
    {
        "title": "REVISION / ORGANIZATION",
        "points": 20,
        "long_description": (
            "This category focuses on the extent to which the essay has been revised, "
            "keeping in mind that revision involves the reworking of ideas while "
            "copy-editing attends to grammar and formatting issues."
        ),
        "ratings": [
            ("EXCEPTIONAL", 20),
            ("IMPRESSIVE", 15),
            ("EMERGING", 10),
            ("DEVELOPING", 5),
        ],
    },
    {
        "title": "GRAMMAR / FORMATTING",
        "points": 10,
        "long_description": (
            "This criterion examines grammar (i.e., sentence structure, phrasing, "
            "spelling, punctuation and other grammar issues) and the overall appearance "
            "of the essay (i.e., margins, font type and size, spacing, and so forth). "
            "Formatting is also closely considered here, whether MLA style, APA style, "
            "or CMS."
        ),
        "ratings": [
            ("EXCEPTIONAL", 10),
            ("IMPRESSIVE", 7),
            ("EMERGING", 5),
            ("DEVELOPING", 2),
        ],
    },
]


# -----------------------------
# Payload construction
# -----------------------------

def build_rubric_payload():
    payload = {
        "rubric[title]": RUBRIC_TITLE,
        "rubric[free_form_criterion_comments]": "1",
        "rubric[hide_score_total]": "0",
    }

    for i, c in enumerate(RUBRIC_CRITERIA):
        base = f"rubric[criteria][{i}]"
        payload[f"{base}[description]"] = c["title"]
        payload[f"{base}[long_description]"] = c["long_description"]
        payload[f"{base}[points]"] = str(c["points"])
        payload[f"{base}[criterion_use_range]"] = "1"

        for j, (label, pts) in enumerate(c["ratings"]):
            payload[f"{base}[ratings][{j}][description]"] = label
            payload[f"{base}[ratings][{j}][points]"] = str(pts)

    return payload


# -----------------------------
# Main
# -----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=env("CANVAS_BASE_URL"))
    ap.add_argument("--token", default=env("CANVAS_TOKEN"))
    ap.add_argument("--course-id", default=env("COURSE_ID"))
    ap.add_argument("--assignment-id", default=env("ASSIGNMENT_ID"))
    args = ap.parse_args()

    if not args.base_url or not args.token or not args.course_id:
        print("Missing Canvas configuration.", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    session.headers.update(headers(args.token))
    base = args.base_url.rstrip("/")

    # Create rubric
    payload = build_rubric_payload()
    r = session.post(
        f"{base}/api/v1/courses/{args.course_id}/rubrics",
        data=payload,
    )

    if r.status_code >= 400:
        print("Rubric creation failed:", r.text, file=sys.stderr)
        sys.exit(1)

    rubric = r.json().get("rubric", r.json())
    rubric_id = rubric["id"]
    print(f"Created rubric '{RUBRIC_TITLE}' (id={rubric_id})")

    # Optional attachment
    if args.assignment_id:
        a = session.post(
            f"{base}/api/v1/courses/{args.course_id}/rubric_associations",
            data={
                "rubric_association[association_type]": "Assignment",
                "rubric_association[association_id]": args.assignment_id,
                "rubric_association[rubric_id]": rubric_id,
                "rubric_association[purpose]": "grading",
                "rubric_association[use_for_grading]": "1",
            },
        )
        if a.status_code >= 400:
            print("Failed to attach rubric:", a.text, file=sys.stderr)
            sys.exit(1)

        print(f"Attached rubric to assignment {args.assignment_id}")

    print("SUCCESS")


if __name__ == "__main__":
    main()
