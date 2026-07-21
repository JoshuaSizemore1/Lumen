"""Read-only Canvas REST client. Auth is the browser session cookie (lifted from
the UI login window and handed over IPC); this module never sees the password and
never writes to Canvas. The httpx.Client is injected so tests use MockTransport.

Proven against utah.instructure.com in the 2026-07-20 spike: session cookies from
an embedded QtWebEngine login authenticate these exact endpoints."""

import json

import httpx

_UA = "Mozilla/5.0 (Lumen)"


class CanvasSessionExpired(Exception):
    """HTTP 401 — the session cookie is no longer valid; the UI must re-login."""


def _strip(body: str) -> str:
    """Canvas prefixes some API JSON with `while(1);` anti-hijack junk."""
    return body[len("while(1);"):] if body.startswith("while(1);") else body


def _next_link(link_header: str) -> str | None:
    """URL marked rel="next" in an RFC5988 Link header, else None."""
    for part in link_header.split(","):
        bits = part.split(";")
        if len(bits) < 2:
            continue
        url = bits[0].strip().lstrip("<").rstrip(">")
        if 'rel="next"' in "".join(bits[1:]):
            return url
    return None


class CanvasClient:
    def __init__(self, base_url: str, client: httpx.Client):
        self._base = base_url.rstrip("/")
        self._c = client

    @classmethod
    def with_cookies(cls, base_url: str, cookies: dict[str, str]) -> "CanvasClient":
        base = base_url.rstrip("/")
        client = httpx.Client(
            base_url=base, cookies=cookies,
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=25.0, follow_redirects=False)
        return cls(base, client)

    def close(self) -> None:
        self._c.close()

    def _get(self, path: str, **params):
        r = self._c.get(path, params=params or None)
        if r.status_code == 401:
            raise CanvasSessionExpired(path)
        r.raise_for_status()
        return json.loads(_strip(r.text))

    def _paginate(self, path: str, **params) -> list[dict]:
        out: list[dict] = []
        r = self._c.get(path, params={**params, "per_page": 100})
        seen: set[str] = set()
        while True:
            if r.status_code == 401:
                raise CanvasSessionExpired(str(r.request.url))
            r.raise_for_status()
            page = json.loads(_strip(r.text))
            if isinstance(page, list):
                out.extend(x for x in page if isinstance(x, dict))
            nxt = _next_link(r.headers.get("link", ""))  # httpx headers are case-insensitive
            if not nxt or nxt in seen:  # seen-guard: a cyclic Link can never hang the daemon
                break
            seen.add(nxt)
            r = self._c.get(nxt)  # request the Link URL as-is; it carries page/per_page
        return out

    def me(self) -> dict:
        return self._get("/api/v1/users/self")

    def courses(self, state: str = "active") -> list[dict]:
        raw = self._paginate("/api/v1/courses", enrollment_state=state)
        return [{"id": c["id"],
                 "name": c.get("name") or c.get("course_code") or "?",
                 "course_code": c.get("course_code")}
                for c in raw if c.get("id")]

    def assignments(self, course_id: int) -> list[dict]:
        raw = self._paginate(f"/api/v1/courses/{course_id}/assignments",
                             **{"include[]": "submission"})
        return [self._norm_assignment(course_id, a) for a in raw if a.get("id")]

    @staticmethod
    def _norm_assignment(course_id: int, a: dict) -> dict:
        sub = a.get("submission") or {}
        submitted = (bool(sub.get("submitted_at"))
                     or sub.get("workflow_state") in ("submitted", "graded", "complete"))
        return {"id": a["id"], "course_id": course_id,
                "name": a.get("name") or "(untitled)", "due_at": a.get("due_at"),
                "points": a.get("points_possible"), "html_url": a.get("html_url"),
                "description": a.get("description"), "submitted": submitted}

    def announcements(self, course_id: int) -> list[dict]:
        raw = self._paginate(f"/api/v1/courses/{course_id}/discussion_topics",
                             only_announcements=True)
        return [{"id": t["id"], "course_id": course_id,
                 "title": t.get("title") or "(untitled)",
                 "posted_at": t.get("posted_at") or t.get("created_at"),
                 "message": t.get("message"), "html_url": t.get("html_url")}
                for t in raw if t.get("id")]
