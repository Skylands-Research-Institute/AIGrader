from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests


@dataclass(frozen=True)
class CanvasAuth:
    base_url: str
    token: str


class CanvasAPIError(RuntimeError):
    def __init__(self, method: str, url: str, status_code: int, body: str):
        super().__init__(f"{method} {url} -> {status_code}: {body}")
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body = body


class CanvasClient:
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

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, Any] | None = None,
        data: Dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        url = self._url(path)
        last: Optional[CanvasAPIError] = None

        for attempt in range(1, self.max_retries + 1):
            resp = self.session.request(
                method,
                url,
                params=params,
                data=data,
                json=json,
                timeout=self.timeout_s,
            )

            if resp.status_code < 400:
                if resp.text.strip() == "":
                    return None
                return resp.json()

            if resp.status_code in (500, 502, 503, 504):
                last = CanvasAPIError(method, url, resp.status_code, resp.text)
                time.sleep(self.retry_backoff_s * attempt)
                continue

            raise CanvasAPIError(method, url, resp.status_code, resp.text)

        if last:
            raise last
        raise CanvasAPIError(method, url, 599, "Unknown error after retries")

    def _get_paginated(self, path: str, *, params: Dict[str, Any] | None = None) -> List[Any]:
        url = self._url(path)
        out: List[Any] = []
        p = dict(params or {})
        p.setdefault("per_page", 100)

        while url:
            resp = self.session.get(url, params=p, timeout=self.timeout_s)

            if resp.status_code >= 400:
                if resp.status_code in (500, 502, 503, 504):
                    ok = False
                    last = resp
                    for attempt in range(1, self.max_retries + 1):
                        time.sleep(self.retry_backoff_s * attempt)
                        resp = self.session.get(url, params=p, timeout=self.timeout_s)
                        if resp.status_code < 400:
                            ok = True
                            break
                        last = resp
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
            p = {}

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

    # -----------------------------
    # Canvas Files -> prompt loading
    # -----------------------------

    def _find_course_folder_id_by_name(self, course_id: int, folder_name: str) -> int:
        """
        Find a course folder by name using the Canvas folders search_term filter.

        Selection rule (to avoid surprises):
          1) exact name match AND full_name endswith "/<folder_name>"
          2) exact name match (if full_name missing)
        Fail-fast if no matches.
        """
        folder_name = folder_name.strip()
        if not folder_name:
            raise ValueError("folder_name must be non-empty.")

        folders = self._get_paginated(
            f"/api/v1/courses/{course_id}/folders",
            params={"search_term": folder_name, "per_page": 100},
        )

        exact: List[Dict[str, Any]] = []
        for f in folders:
            if not isinstance(f, dict):
                continue
            name = f.get("name")
            if isinstance(name, str) and name.strip() == folder_name:
                exact.append(f)

        if not exact:
            raise FileNotFoundError(f'Canvas folder not found by name search_term="{folder_name}".')

        preferred: List[Dict[str, Any]] = []
        suffix = "/" + folder_name
        for f in exact:
            full_name = f.get("full_name")
            if isinstance(full_name, str) and full_name.endswith(suffix):
                preferred.append(f)

        if len(preferred) == 1:
            chosen = preferred[0]
        elif len(preferred) > 1:
            preferred.sort(key=lambda x: len(str(x.get("full_name") or "")))
            chosen = preferred[0]
        else:
            chosen = exact[0]

        fid = chosen.get("id")
        if fid is None:
            raise RuntimeError("Canvas folder search returned an item without an id.")
        return int(fid)

    def get_course_file_text(self, course_id: int, folder_path: str, filename: str) -> str:
        """
        Load a text file from Canvas course Files, by folder name + filename.

        We treat folder_path as a *folder name* (not a full nested path),
        because Canvas folder path resolution is brittle across instances.

        Example:
          get_course_file_text(course_id, "AIGrader", "initial_prompt.txt")

        Fail-fast behavior:
          - raises FileNotFoundError if folder or file does not exist
          - raises RuntimeError if download fails or content is empty
        """
        folder_name = folder_path.strip().strip("/")
        want = filename.strip()

        if not folder_name:
            raise ValueError("folder_path must be non-empty (e.g., 'AIGrader').")
        if not want:
            raise ValueError("filename must be non-empty (e.g., 'initial_prompt.txt').")

        folder_id = self._find_course_folder_id_by_name(course_id, folder_name)

        files = self._get_paginated(f"/api/v1/folders/{folder_id}/files", params={"per_page": 100})
        match = None
        for f in files:
            if not isinstance(f, dict):
                continue
            fn = f.get("filename") or f.get("display_name") or ""
            if isinstance(fn, str) and fn.strip() == want:
                match = f
                break

        if match is None:
            raise FileNotFoundError(f"Canvas file not found: {folder_name}/{want}")

        download_url = match.get("url") or match.get("download_url")
        if not isinstance(download_url, str) or not download_url.strip():
            fid = match.get("id")
            if fid is None:
                raise RuntimeError("Canvas file metadata missing download URL and id.")
            detail = self._request("GET", f"/api/v1/files/{fid}")
            download_url = detail.get("url") or detail.get("download_url")

        if not isinstance(download_url, str) or not download_url.strip():
            raise RuntimeError("Canvas did not provide a downloadable URL for the file.")

        resp = self.session.get(download_url, timeout=self.timeout_s, allow_redirects=True)
        if resp.status_code >= 400:
            raise CanvasAPIError("GET", download_url, resp.status_code, resp.text)

        # IMPORTANT: Canvas text file downloads sometimes get decoded with the wrong charset guess.
        # Force UTF-8 to prevent mojibake like â â for smart quotes.
        resp.encoding = "utf-8"

        text = resp.text.replace("\r\n", "\n").strip()
        if not text:
            raise RuntimeError(f"Canvas file {folder_name}/{want} is empty.")
        return text

    # -----------------------------
    # Submissions / comments
    # -----------------------------

    def get_submission_with_comments(self, course_id: int, assignment_id: int, user_id: int) -> Dict[str, Any]:
        """
        Fetch a submission including submission_comments so we can detect existing AIGrader assessments.
        """
        return self._request(
            "GET",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}",
            params={"include[]": ["submission_comments", "submission_history", "user"]},
        )

    # -----------------------------
    # Submission comment posting
    # -----------------------------

    def add_submission_comment(
        self,
        course_id: int,
        assignment_id: int,
        user_id: int,
        text_comment: str,
        *,
        as_html: bool = False,
        attempt: int | None = None,
    ) -> Dict[str, Any]:
        path = f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}"

        payload: Dict[str, Any] = {"comment[text_comment]": text_comment}
        if attempt is not None:
            payload["comment[attempt]"] = int(attempt)

        return self._request("PUT", path, data=payload)

    # -----------------------------
    # High-level API methods
    # -----------------------------

    def get_assignment(self, course_id: int, assignment_id: int, *, include: Optional[List[str]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if include:
            params["include[]"] = include
        return self._request("GET", f"/api/v1/courses/{course_id}/assignments/{assignment_id}", params=params)

    @staticmethod
    def _has_criteria(r: Any) -> bool:
        if not isinstance(r, dict):
            return False
        c = r.get("criteria") or r.get("data")
        return isinstance(c, list) and len(c) > 0

    @staticmethod
    def _looks_like_criteria_list(x: Any) -> bool:
        if not isinstance(x, list) or not x:
            return False
        first = x[0]
        if not isinstance(first, dict):
            return False
        return ("points" in first) and (("description" in first) or ("criterion_description" in first))

    def _fetch_rubric_by_id(self, course_id: int, rubric_id: Any) -> Optional[Dict[str, Any]]:
        if rubric_id is None:
            return None

        rid = str(rubric_id).strip()
        if rid.startswith("_"):
            rid = rid[1:]

        try:
            full = self._request("GET", f"/api/v1/courses/{course_id}/rubrics/{rid}")
            if isinstance(full, dict):
                return full
        except CanvasAPIError as e:
            if e.status_code != 404:
                raise

        try:
            full = self._request("GET", f"/api/v1/rubrics/{rid}")
            if isinstance(full, dict):
                return full
        except CanvasAPIError as e:
            if e.status_code != 404:
                raise

        return None

    def get_rubric_for_assignment(self, course_id: int, assignment_id: int) -> Optional[Dict[str, Any]]:
        try:
            assocs = self._request(
                "GET",
                f"/api/v1/courses/{course_id}/rubric_associations",
                params={"association_type": "Assignment", "association_id": assignment_id, "per_page": 100},
            )

            if isinstance(assocs, list) and assocs:
                match = None
                for a in assocs:
                    if a.get("association_type") == "Assignment" and str(a.get("association_id")) == str(assignment_id):
                        match = a
                        break
                match = match or assocs[0]

                embedded = match.get("rubric")
                if isinstance(embedded, dict) and self._has_criteria(embedded):
                    return embedded

                if isinstance(embedded, dict):
                    rid = embedded.get("id") or match.get("rubric_id")
                    full = self._fetch_rubric_by_id(course_id, rid)
                    if full and self._has_criteria(full):
                        return full

                full = self._fetch_rubric_by_id(course_id, match.get("rubric_id"))
                if full and self._has_criteria(full):
                    return full

        except CanvasAPIError as e:
            if e.status_code != 404:
                raise

        a = self.get_assignment(course_id, assignment_id, include=["rubric", "rubric_settings", "rubric_association"])
        embedded = a.get("rubric")

        if self._looks_like_criteria_list(embedded):
            return {"criteria": embedded}

        if isinstance(embedded, dict):
            if self._has_criteria(embedded):
                return embedded
            rid = embedded.get("id")
            full = self._fetch_rubric_by_id(course_id, rid)
            if full and self._has_criteria(full):
                return full

        if isinstance(embedded, list) and embedded and isinstance(embedded[0], dict):
            first = embedded[0]
            if self._has_criteria(first):
                return first
            rid = first.get("id")
            full = self._fetch_rubric_by_id(course_id, rid)
            if full and self._has_criteria(full):
                return full

        rs = a.get("rubric_settings") or {}
        if isinstance(rs, dict):
            rid = rs.get("rubric_id") or rs.get("id")
            full = self._fetch_rubric_by_id(course_id, rid)
            if full and self._has_criteria(full):
                return full

        ra = a.get("rubric_association") or {}
        if isinstance(ra, dict):
            rid = ra.get("rubric_id")
            full = self._fetch_rubric_by_id(course_id, rid)
            if full and self._has_criteria(full):
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
