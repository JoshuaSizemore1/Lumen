import httpx
import pytest

from lumen.daemon.connectors.canvas_client import CanvasClient, CanvasSessionExpired

BASE = "https://x.instructure.com"


def client_for(handler):
    return CanvasClient(BASE, httpx.Client(
        base_url=BASE, transport=httpx.MockTransport(handler)))


def test_courses_normalizes_name_fallback_and_strips_while1(tmp_path):
    def handler(req):
        # Canvas anti-hijack prefix + a course with no name (fall back to code).
        return httpx.Response(
            200,
            text='while(1);[{"id":1,"name":"CS 3505","course_code":"CS3505"},'
                 '{"id":2,"course_code":"MATH"}]')
    got = client_for(handler).courses()
    assert got == [
        {"id": 1, "name": "CS 3505", "course_code": "CS3505"},
        {"id": 2, "name": "MATH", "course_code": "MATH"},
    ]


def test_assignments_derive_submitted_from_submission(tmp_path):
    def handler(req):
        return httpx.Response(200, json=[
            {"id": 10, "name": "HW1", "due_at": "2026-01-17T06:59:59Z",
             "points_possible": 110.0, "html_url": "u1", "description": "<p>do it</p>",
             "submission": {"workflow_state": "unsubmitted", "submitted_at": None}},
            {"id": 11, "name": "HW2", "due_at": None, "points_possible": 40.0,
             "html_url": "u2", "description": None,
             "submission": {"workflow_state": "graded", "submitted_at": "2026-02-01T00:00:00Z",
                            "score": 38.5}},
        ])
    got = client_for(handler).assignments(1)
    assert got[0] == {"id": 10, "course_id": 1, "name": "HW1",
                      "due_at": "2026-01-17T06:59:59Z", "points": 110.0, "score": None,
                      "html_url": "u1", "description": "<p>do it</p>", "submitted": False,
                      "updated_at": None, "submission_types": None, "is_quiz": False}
    assert got[1]["submitted"] is True
    assert got[1]["score"] == 38.5        # graded → the earned score is captured


def test_announcements_normalized(tmp_path):
    def handler(req):
        return httpx.Response(200, json=[
            {"id": 5, "title": "Final Grades", "posted_at": "2026-05-01T16:15:40Z",
             "message": "<p>done</p>", "html_url": "a5"}])
    got = client_for(handler).announcements(1)
    assert got == [{"id": 5, "course_id": 1, "title": "Final Grades",
                    "posted_at": "2026-05-01T16:15:40Z", "message": "<p>done</p>",
                    "html_url": "a5"}]


def test_401_raises_session_expired(tmp_path):
    def handler(req):
        return httpx.Response(401, json={"status": "unauthenticated"})
    with pytest.raises(CanvasSessionExpired):
        client_for(handler).me()


def test_pagination_follows_link_next(tmp_path):
    def handler(req):
        if "page=2" in str(req.url):
            return httpx.Response(200, json=[{"id": 2, "course_code": "B"}])
        return httpx.Response(
            200, json=[{"id": 1, "course_code": "A"}],
            headers={"Link": f'<{BASE}/api/v1/courses?page=2>; rel="next"'})
    got = client_for(handler).courses()
    assert [c["id"] for c in got] == [1, 2]


def test_assignments_capture_type_hints_for_calendar_sync():
    """submission_types + is_quiz let BASIC mode name the kind of thing an
    assignment is without asking a model."""
    def handler(req):
        return httpx.Response(200, json=[
            {"id": 12, "name": "Midterm", "submission_types": ["online_quiz"],
             "quiz_id": 77, "updated_at": "2026-08-01T00:00:00Z"},
            {"id": 13, "name": "Essay", "submission_types": ["online_upload"]},
        ])
    got = client_for(handler).assignments(1)
    assert got[0]["submission_types"] == "online_quiz"
    assert got[0]["is_quiz"] is True
    assert got[0]["updated_at"] == "2026-08-01T00:00:00Z"
    assert got[1]["submission_types"] == "online_upload"
    assert got[1]["is_quiz"] is False


def test_assignments_tolerate_a_string_submission_type():
    """Canvas has been seen returning a bare string here, not a list."""
    def handler(req):
        return httpx.Response(200, json=[
            {"id": 14, "name": "X", "submission_types": "online_text_entry"}])
    assert client_for(handler).assignments(1)[0]["submission_types"] == "online_text_entry"
