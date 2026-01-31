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

            last = CanvasAPIError(method, url, resp.status_code, resp.text)
            if attempt < self.max_retries:
                time.sleep(self.retry_backoff_s * attempt)

        assert last is not None
        raise last

    def _get_paginated(self, path: str, *, params: Dict[str, Any] | None = None) -> List[Any]:
        """
        Canvas API pagination: follows Link headers and aggregates results.
        """
        url = self._url(path)
        out: List[Any] = []
        params = dict(params or {})

        while True:
            resp = self.session.get(url, params=params, timeout=self.timeout_s)
            if resp.status_code >= 400:
                raise CanvasAPIError("GET", url, resp.status_code, resp.text)

            data = resp.json()
            if isinstance(data, list):
                out.extend(data)
            else:
                out.append(data)

            link = resp.headers.get("Link", "")
            next_url = None
            if link:
                parts = [p.strip() for p in link.split(",")]
                for p in parts:
                    if 'rel="next"' in p:
                        start = p.find("<")
                        end = p.find(">")
                        if start != -1 and end != -1 and end > start:
                            next_url = p[start + 1 : end]
                        break

            if not next_url:
                break

            url = next_url
            params = {}

        return out

    # -----------------------------
    # Courses / assignments
    # -----------------------------

    def get_course(self, course_id: int) -> Dict[str, Any]:
        return self._request("GET", f"/api/v1/courses/{course_id}")

    def get_assignment(self, course_id: int, assignment_id: int, *, include: Optional[List[str]] = None) -> Dict[str, Any]:
        params = {}
        if include:
            params["include[]"] = include
        return self._request("GET", f"/api/v1/courses/{course_id}/assignments/{assignment_id}", params=params)

    # -----------------------------
    # Rubrics (KEEP ORIGINAL LOGIC)
    # -----------------------------

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

    def _fetch_rubric_by_id(self, course_id: int, rubric_id: Any) -> Optional[Dict[str, Any]]:
        if rubric_id is None:
            return None
        try:
            r = self._request("GET", f"/api/v1/courses/{course_id}/rubrics/{rubric_id}")
            return r if isinstance(r, dict) else None
        except CanvasAPIError as e:
            if e.status_code == 404:
                return None
            raise

    def _looks_like_criteria_list(self, x: Any) -> bool:
        return isinstance(x, list) and x and isinstance(x[0], dict) and ("points" in x[0] or "ratings" in x[0])

    def _has_criteria(self, rubric: Dict[str, Any]) -> bool:
        if "data" in rubric and isinstance(rubric["data"], list) and rubric["data"]:
            return True
        if "criteria" in rubric and isinstance(rubric["criteria"], (list, dict)) and rubric["criteria"]:
            return True
        return False

    # -----------------------------
    # Submissions
    # -----------------------------

    def get_submission_text_entry(
        self,
        course_id: int,
        assignment_id: int,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        # ✅ CHANGE: include "attachments" so docx uploads can be discovered
        include = ["submission_history", "user", "attachments"]

        if user_id is not None:
            return self._request(
                "GET",
                f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}",
                params={"include[]": include},
            )

        subs = self._get_paginated(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions",
            params={"include[]": include, "per_page": 100},
        )

        for s in subs:
            body = s.get("body")
            if isinstance(body, str) and body.strip():
                return s

        return subs[0] if subs else None

    def get_submission_with_comments(self, course_id: int, assignment_id: int, user_id: int) -> Dict[str, Any]:
        # ✅ CHANGE: include "attachments" here too
        return self._request(
            "GET",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}",
            params={"include[]": ["submission_comments", "submission_history", "user", "attachments"]},
        )

    # -----------------------------
    # Course Files (for prompts)
    # -----------------------------

    def _find_course_folder_id_by_name(self, course_id: int, folder_name: str) -> int:
        folders = self._get_paginated(f"/api/v1/courses/{course_id}/folders", params={"per_page": 100})
        for f in folders:
            if not isinstance(f, dict):
                continue
            name = f.get("name")
            if isinstance(name, str) and name.strip() == folder_name:
                fid = f.get("id")
                if isinstance(fid, int):
                    return fid
        raise FileNotFoundError(f"Canvas folder not found: {folder_name}")

    def get_course_file_text(self, course_id: int, folder_path: str, filename: str) -> str:
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

        resp.encoding = "utf-8"
        text = resp.text.replace("\r\n", "\n").strip()
        if not text:
            raise RuntimeError(f"Canvas file {folder_name}/{want} is empty.")
        return text

    # -----------------------------
    # ✅ NEW: file download helper for DOCX attachments
    # -----------------------------

    def download_file_bytes(self, download_url: str) -> bytes:
        """
        Download a file (Canvas attachment url / download_url) and return raw bytes.
        Intended for submission attachments like .docx.
        """
        if not isinstance(download_url, str) or not download_url.strip():
            raise ValueError("download_url must be a non-empty string.")

        resp = self.session.get(download_url, timeout=self.timeout_s, allow_redirects=True)
        if resp.status_code >= 400:
            raise CanvasAPIError("GET", download_url, resp.status_code, resp.text)

        data = resp.content
        if not data:
            raise RuntimeError("Downloaded file is empty.")
        return data


    def get_assignment_description(
        self,
        course_id: int,
        assignment_id: int,
    ) -> str:
        """
        Fetch the assignment description (often HTML).
        
        Args:
            course_id: Canvas course ID
            assignment_id: Canvas assignment ID
            
        Returns:
            Assignment description as string (may be HTML), or empty string if not available
        """
        try:
            assignment = self.get_assignment(course_id, assignment_id)
            if isinstance(assignment, dict):
                desc = assignment.get("description") or ""
                return desc if isinstance(desc, str) else ""
        except Exception:
            # If we can't fetch it, return empty rather than failing
            pass
        return ""

    def add_submission_comment(
        self,
        course_id: int,
        assignment_id: int,
        user_id: int,
        text_comment: str,
        *,
        as_html: bool=False,
        attempt: int | None=None,
    ) -> Dict[str, Any]:
        path = f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}"
    
        payload: Dict[str, Any] = {"comment[text_comment]": text_comment}
        if attempt is not None:
            payload["comment[attempt]"] = int(attempt)
    
        return self._request("PUT", path, data=payload)
