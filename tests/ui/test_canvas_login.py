from lumen.ui_v3 import canvas_login as cl


def test_collect_cookies_dedups_last_wins():
    got = cl.collect_cookies([("canvas_session", "old"), ("_csrf_token", "t"),
                              ("canvas_session", "new")])
    assert got == {"canvas_session": "new", "_csrf_token": "t"}


def test_is_authenticated_true_only_with_session_cookie():
    assert cl.is_authenticated(["_csrf_token", "canvas_session"]) is True
    assert cl.is_authenticated(["_csrf_token", "log_session_id"]) is False


def test_autofill_js_embeds_values_and_is_guarded():
    js = cl.autofill_js("u1234567", "p@ss'\"")
    assert "u1234567" in js
    assert "try" in js and "catch" in js       # never throws into the page
    assert "p@ss" in js                          # value carried, JS-escaped
