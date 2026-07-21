"""Pure helpers for the Canvas login screen (screens/canvas.py). Kept Qt-free and
module-level so they unit-test without a web engine: cookie collection, login
detection, and the best-effort autofill JS. The Qt screen imports these."""

import json

AUTH_COOKIE = "canvas_session"


def collect_cookies(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Flatten (name, value) cookie pairs to a dict, last value winning. The whole
    dict is forwarded to the daemon; extra cookies are harmless to the API."""
    out: dict[str, str] = {}
    for name, value in pairs:
        out[name] = value
    return out


def is_authenticated(names) -> bool:
    """The session is live once Canvas has set its httpOnly session cookie."""
    return AUTH_COOKIE in set(names)


def autofill_js(unid: str, password: str) -> str:
    """Best-effort fill of the U-of-U / CAS login form. Tries a few common field
    selectors; wrapped so a drifted form can never throw into the page (it just
    fills nothing and the user types manually)."""
    u = json.dumps(unid)          # json.dumps yields a safely-escaped JS string
    p = json.dumps(password)
    return f"""(function() {{
  try {{
    var u = {u}, p = {p};
    var us = document.querySelector('#username, input[name=username], input[name=j_username]');
    var ps = document.querySelector('#password, input[name=password], input[name=j_password]');
    if (us) us.value = u;
    if (ps) ps.value = p;
  }} catch (e) {{ /* form not present / selectors drifted — leave it to the user */ }}
}})();"""
