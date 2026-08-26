import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lumen.ui_v3 import canvas_login as cl

JS_DIR = Path(__file__).parent / "js"


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


# --- cookie-domain narrowing (P3) --------------------------------------------
@pytest.mark.parametrize("base, host", [
    ("https://utah.instructure.com", "utah.instructure.com"),
    ("https://utah.instructure.com/", "utah.instructure.com"),
    ("http://Canvas.Example.EDU:8443/x", "canvas.example.edu"),
    ("", ""),
])
def test_cookie_host_parses_the_base_url(base, host):
    assert cl.cookie_host(base) == host


@pytest.mark.parametrize("domain, ok", [
    ("utah.instructure.com", True),
    (".utah.instructure.com", True),
    (".instructure.com", True),          # wildcard parent
    ("instructure.com", True),
    ("duosecurity.com", False),
    ("shib.utah.edu", False),
    ("evil-utah.instructure.com.attacker.net", False),
    ("", False),
])
def test_is_canvas_cookie_matches_only_the_canvas_host(domain, ok):
    assert cl.is_canvas_cookie(domain, "utah.instructure.com") is ok


# --- selector sharing --------------------------------------------------------
def test_password_type_fallback_is_present():
    """The drift-proof selector: whatever the id/name became, a CAS password box
    is still input[type=password]."""
    assert "input[type=password]" in cl.PASS_SELECTORS


def test_capture_and_autofill_share_the_same_selectors():
    """The chicken-and-egg behind #44: capture used its own copy of the selector
    string, so a form it missed was never saved and autofill could never fire."""
    read, fill = cl.read_fields_js(), cl.autofill_js("u", "p")
    for sel in cl.USER_SELECTORS + cl.PASS_SELECTORS:
        assert sel in read, sel
        assert sel in fill, sel


# --- generated-JS structure --------------------------------------------------
def test_autofill_js_uses_the_native_value_setter():
    js = cl.autofill_js("u", "p")
    assert "getOwnPropertyDescriptor" in js and "HTMLInputElement" in js


def test_autofill_js_dispatches_input_and_change():
    js = cl.autofill_js("u", "p")
    assert 'new Event("input", {bubbles: true})' in js
    assert 'new Event("change", {bubbles: true})' in js


def test_autofill_js_retries_and_watches_for_mutations():
    js = cl.autofill_js("u", "p")
    assert "MutationObserver" in js and "setInterval" in js


def test_autofill_js_traverses_same_origin_frames():
    assert "contentDocument" in cl.autofill_js("u", "p")


def test_autofill_js_carries_the_reinjection_sentinel():
    assert "__lumenAutofill" in cl.autofill_js("u", "p")


def test_autofill_js_json_escapes_hostile_values():
    hostile = 'u"; alert(1); //'
    password = "p'\\\n\u2028"
    js = cl.autofill_js(hostile, password)
    # The payload never appears raw — only as an escaped JSON string literal —
    # and it round-trips, so it reaches the field as data rather than as a
    # statement the page would run.
    assert hostile not in js
    cfg = json.loads(js.split("var CFG = ", 1)[1].split(";\n", 1)[0])
    assert cfg["u"] == hostile and cfg["p"] == password


def test_password_submit_branch_only_when_allowed():
    """Guarding the branch in Python, not just at runtime: an accidentally-armed
    submit is a university account lockout, so the JS that could click must not
    even be generated once the budget is spent."""
    armed = cl.autofill_js("u", "p", allow_password_submit=True)
    spent = cl.autofill_js("u", "p", allow_password_submit=False)
    assert '"allowPassSubmit": true' in armed
    assert '"allowPassSubmit": false' in spent


def test_probe_js_never_reads_a_value():
    js = cl.probe_js()
    assert ".value" not in js
    assert "autocomplete" in js and "form_action" in js


# --- parse_autofill_result is total ------------------------------------------
@pytest.mark.parametrize("raw", [
    None, "", "not json", "[]", "null", 17, b"bytes", {"user": 5},
    '{"user": null, "frames": "many", "submitted_password": "yes"}',
])
def test_parse_autofill_result_never_raises(raw):
    got = cl.parse_autofill_result(raw)
    assert set(got) == set(cl.EMPTY_RESULT)
    assert isinstance(got["frames"], int)
    assert isinstance(got["submitted_password"], bool)
    assert isinstance(got["user"], str)


def test_parse_autofill_result_reads_a_real_report():
    got = cl.parse_autofill_result(json.dumps({
        "user": "#username", "pass": "#password", "submitted_password": True,
        "frames": 2, "blocked_frames": 1, "tries": 3, "url": "https://x",
        "done": True}))
    assert got["user"] == "#username" and got["submitted_password"] is True
    assert got["frames"] == 2 and got["tries"] == 3


def test_parse_autofill_result_rejects_bool_as_int():
    """True is an int in Python; a count of `True` would render as 'True frames'."""
    assert cl.parse_autofill_result('{"frames": true}')["frames"] == 0


# --- AutoSubmitPolicy truth table --------------------------------------------
def _r(**kw):
    """A report. `pass` is a keyword, so it is spelled `passwd` here and
    translated — the JS field name stays `pass`."""
    kw["pass"] = kw.pop("passwd", "")
    return {**cl.EMPTY_RESULT, **kw}


def test_policy_starts_armed():
    assert cl.AutoSubmitPolicy().allow_password_submit is True


def test_policy_spends_the_budget_on_a_password_submit():
    p = cl.AutoSubmitPolicy()
    assert p.record(_r(passwd="#password",
                      submitted_password=True)) == cl.SUBMITTED
    assert p.allow_password_submit is False


def test_username_only_page_does_not_spend_the_budget():
    """Two-page CAS: step 1 submits a bare uNID. If that spent the budget the
    password on step 2 would never be auto-submitted at all."""
    p = cl.AutoSubmitPolicy()
    assert p.record(_r(user="#username", submitted_user=True)) == cl.ARMED
    assert p.allow_password_submit is True


def test_password_field_after_a_submit_locks_to_fill_only():
    """The lockout guard: we submitted once and the password box is back, so
    those credentials are wrong. Fill forever after, but never press the button."""
    p = cl.AutoSubmitPolicy()
    p.record(_r(passwd="#password", submitted_password=True))
    assert p.record(_r(passwd="#password")) == cl.FILL_ONLY
    assert p.allow_password_submit is False


def test_fill_only_is_permanent():
    p = cl.AutoSubmitPolicy()
    p.state = cl.FILL_ONLY
    for res in (_r(), _r(submitted_password=True), _r(passwd="#password")):
        assert p.record(res) == cl.FILL_ONLY
    assert p.allow_password_submit is False


def test_no_password_field_after_submit_stays_submitted():
    """Duo's push page has no password box — that is progress, not failure."""
    p = cl.AutoSubmitPolicy()
    p.record(_r(passwd="#password", submitted_password=True))
    assert p.record(_r()) == cl.SUBMITTED


def test_done_and_reset():
    p = cl.AutoSubmitPolicy()
    p.done()
    assert p.state == cl.DONE and p.allow_password_submit is False
    assert p.record(_r(passwd="#password")) == cl.DONE   # inert once done
    p.reset()
    assert p.allow_password_submit is True


def test_policy_tolerates_a_missing_result():
    assert cl.AutoSubmitPolicy().record(None) == cl.ARMED


# --- behavioural: run the real JS in a real engine ---------------------------
@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node not installed — JS behaviour unverified here")
def test_autofill_js_behaves_against_a_stub_dom(tmp_path):
    """Structural assertions could not have caught the original #44 bug: the old
    one-shot `el.value = x` contained every string a shape test would look for.
    So execute the generated script against a DOM stub whose `value` is a native
    accessor, and assert on what actually happened to the fields.

    Node, not Chromium — the suite still never spins up a web engine."""
    snippets = tmp_path / "js.json"
    snippets.write_text(json.dumps({
        "submit": cl.autofill_js("u1234567", "s3cret", allow_password_submit=True),
        "nosubmit": cl.autofill_js("u1234567", "s3cret"),
        "useronly": cl.autofill_js("u1234567", "s3cret", allow_password_submit=True),
        "probe": cl.probe_js(),
        "read": cl.read_fields_js(),
    }))
    proc = subprocess.run(
        ["node", str(JS_DIR / "autofill_spec.js"), str(snippets)],
        capture_output=True, text=True, cwd=JS_DIR, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL" not in proc.stdout, proc.stdout
