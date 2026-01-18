from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests


@dataclass(frozen=True)
class CanvasAuth:
    base_url: str          # e.g. https://sussexccc.instructure.com
    token: str             # Canvas access token


class CanvasAPIError(RuntimeError):
    def __init__(self, method: str, url: str, status_code: int, body: str):
        super().__init__(f"{method} {url} -> {status_code}: {body}")
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body = body


class CanvasClient:
    """
    Minimal Canvas API client for AIGrader Phase 1 preflight.
    """

    def __init__(
        self,
        auth: CanvasAuth,
        *,
        timeout_s: int = 30,
        max_retries: int = 3,
        retry_backoff_s: float = 0.6,
        user_agent: str = "aigrader/0.1",
    ):
        self.auth = CanvasAuth(auth.base_url.rstrip("/"), auth.token)
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.auth.token}",
                "Accept": "application/json",
                "User-Agent": user_agent,
            }
        )

    # -----------------------------
    # Low-level HTTP helpers
    # -----------------------------

    def _url(self, path: str) -> str:
        return urljoin(self.auth.base_url + "/", path.lstrip("/"))

    def _request(self, method: str, path: str, *, params: Dict[str, Any] | None = None) -> Any:
        """
        Make a request with basic retry on transient 5xx.
        Returns parsed JSON.
        Raises CanvasAPIError on errors.
        """
        url = self._url(path)
        last: Optional[CanvasAPIError] = None

        for attempt in range(1, self.max_retries + 1):
            resp = self.session.request(method, url, params=params, timeout=self.timeout_s)

            if resp.status_code < 400:
                if resp.text.strip() == "":
                    return None
                return resp.json()

            # Retry on transient server errors
            if resp.status_code in (500, 502, 503, 504):
                last = CanvasAPIError(method, url, resp.status_code, resp.text)
                time.sleep(self.retry_backoff_s * attempt)
                continue

            raise CanvasAPIError(method, url, resp.status_code, resp.text)

        # Retries exhausted
        if last:
            raise last
        raise CanvasAPIError(method, url, 599, "Unknown error after retries")

    def _get_paginated(self, path: str, *, params: Dict[str, Any] | None = None) -> List[Any]:
        """
        Fetch all pages for a Canvas list endpoint using the Link header.
        """
        url = self._url(path)
        out: List[Any] = []
        p = dict(params or {})
        p.setdefault("per_page", 100)

        while url:
            resp = self.session.get(url, params=p, timeout=self.timeout_s)

            if resp.status_code >= 400:
                # Retry 5xx
                if resp.status_code in (500, 502, 503, 504):
                    ok = False
                    last = None
                    for attempt in range(1, self.max_retries + 1):
                        last = resp
                        time.sleep(self.retry_backoff_s * attempt)
                        resp = self.session.get(url, params=p, timeout=self.timeout_s)
                        if resp.status_code < 400:
                            ok = True
                            break
                    if not ok:
                        raise CanvasAPIError("GET", url, last.status_code, last.text)
                else:
                    raise CanvasAPIError("GET", url, resp.status_code, resp.text)

            data = resp.json()
            if not isinstance(data, list):
                raise RuntimeError(f"Expected list JSON from {url}, got {type(data)}")

            out.extend(data)

            link = resp.headers.get("Link", "")
            url = self._next_link(link)
            p = {}  # next URL already contains query

        return out

    @staticmethod
    def _next_link(link_header: str) -> Optional[str]:
        if not link_header:
            return None
        parts = [p.strip() for p in link_header.split(",")]
        for part in parts:
            if 'rel="next"' in part:
                start = part.find("<")
                end = part.find(">")
                if start != -1 and end != -1 and end > start:
                    return part[start + 1 : end]
        return None

    def add_submission_comment(
        self,
        course_id: int,
        assignment_id: int,
        user_id: int,
        text_comment: str,
        *,
        as_html: bool = False,
    ) -> Dict[str, Any]:
        """
        Add a comment to a submission.

        IMPORTANT:
        - We do NOT send rubric_assessment
        - We do NOT set posted_grade
        - This is review-only

        Canvas comment formatting:
        - Plain text supports newlines. Avoid relying on spaces/tabs alignment.
        """
        path = f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}"

        # Canvas supports either comment[text_comment] (plain) or comment[html_comment] (HTML).
        # You requested plain text: use text_comment by default.
        data: Dict[str, Any]
        if as_html:
            data = {"comment[html_comment]": text_comment}
        else:
            data = {"comment[text_comment]": text_comment}

        return self._request("PUT", path, data=data)

    # -----------------------------
    # High-level API methods
    # -----------------------------

    def get_assignment(self, course_id: int, assignment_id: int, *, include: Optional[List[str]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if include:
            # requests encodes list as repeated include[]= entries if we pass a list to the key
            params["include[]"] = include
        return self._request("GET", f"/api/v1/courses/{course_id}/assignments/{assignment_id}", params=params)

    def get_rubric_for_assignment(self, course_id: int, assignment_id: int) -> Optional[Dict[str, Any]]:
        """
        Robust rubric lookup that returns a FULL rubric with criteria.

        Many Canvas installs return a rubric "summary" object without criteria; in that case
        we follow up by fetching /courses/:course_id/rubrics/:rubric_id.
        """

        def has_criteria(r: Any) -> bool:
            if not isinstance(r, dict):
                return False
            c = r.get("criteria") or r.get("data")
            return isinstance(c, list) and len(c) > 0

        def fetch_full(rubric_id: Any) -> Optional[Dict[str, Any]]:
            if rubric_id is None:
                return None

            rid = str(rubric_id).strip()
            # Canvas sometimes prefixes rubric IDs with "_" in some payloads
            if rid.startswith("_"):
                rid = rid[1:]

            # Try course-scoped first
            try:
                full = self._request("GET", f"/api/v1/courses/{course_id}/rubrics/{rid}")
                return full
            except CanvasAPIError as e:
                if e.status_code != 404:
                    raise

            # Fallback: global rubric endpoint (works when rubric isn't course-owned)
            try:
                full = self._request("GET", f"/api/v1/rubrics/{rid}")
                return full
            except CanvasAPIError as e:
                if e.status_code != 404:
                    raise

            # Last attempt: sometimes include[] helps (harmless if ignored)
            try:
                full = self._request(
                    "GET",
                    f"/api/v1/rubrics/{rid}",
                    params={"include[]": ["criteria", "ratings"]},
                )
                return full
            except CanvasAPIError as e:
                if e.status_code != 404:
                    raise

            return None

        # Attempt A: rubric_associations endpoint (may 404 on your instance)
        assoc_path = f"/api/v1/courses/{course_id}/rubric_associations"
        params = {"association_type": "Assignment", "association_id": assignment_id, "per_page": 100}

        try:
            assocs = self._request("GET", assoc_path, params=params)
            if assocs:
                match = None
                for a in assocs:
                    if a.get("association_type") == "Assignment" and str(a.get("association_id")) == str(assignment_id):
                        match = a
                        break
                match = match or assocs[0]

                embedded = match.get("rubric")
                if isinstance(embedded, dict):
                    if has_criteria(embedded):
                        return embedded
                    rid = embedded.get("id") or match.get("rubric_id")
                    full = fetch_full(rid)
                    if full:
                        return full

                rid = match.get("rubric_id")
                full = fetch_full(rid)
                if full:
                    return full

        except CanvasAPIError as e:
            if e.status_code != 404:
                raise

        # Attempt B: assignment includes (common fallback)
        a = self.get_assignment(course_id, assignment_id, include=["rubric", "rubric_association"])

        embedded = a.get("rubric")
        if isinstance(embedded, dict):
            if has_criteria(embedded):
                return embedded
            rid = embedded.get("id")
            full = fetch_full(rid)
            if full:
                return full

        if isinstance(embedded, list) and embedded and isinstance(embedded[0], dict):
            first = embedded[0]
            if has_criteria(first):
                return first
            rid = first.get("id")
            full = fetch_full(rid)
            if full:
                return full

        ra = a.get("rubric_association")
        if isinstance(ra, dict):
            embedded2 = ra.get("rubric")
            if isinstance(embedded2, dict):
                if has_criteria(embedded2):
                    return embedded2
                rid = embedded2.get("id") or ra.get("rubric_id")
                full = fetch_full(rid)
                if full:
                    return full

            rid = ra.get("rubric_id")
            full = fetch_full(rid)
            if full:
                return full

        rs = a.get("rubric_settings")
        if isinstance(rs, dict):
            rid = rs.get("id") or rs.get("rubric_id")
            full = fetch_full(rid)
            if full:
                return full

        return None

    def get_submission_text_entry(
        self,
        course_id: int,
        assignment_id: int,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        if user_id is not None:
            return self._request(
                "GET",
                f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}",
                params={"include[]": ["submission_history", "user"]},
            )

        subs = self._get_paginated(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions",
            params={"include[]": ["submission_history", "user"], "per_page": 100},
        )

        for s in subs:
            body = s.get("body")
            if isinstance(body, str) and body.strip():
                return s

        return subs[0] if subs else None
