"""Pure helpers for the Canvas login screen (screens/canvas.py). Kept Qt-free and
module-level so they unit-test without a web engine: cookie collection, login
detection, autofill, and the diagnostic probe. The Qt screen imports these.

Debugging the live CAS form (#44) — Josh is the only one who can drive it:

    QTWEBENGINE_REMOTE_DEBUGGING=9222 lumen     # then open localhost:9222
    LUMEN_CANVAS_DEBUG=1 lumen                  # adds Probe / Test fill buttons

`probe_js()` dumps the real field names and frame layout without ever reading a
value, which is what settles a selector drift in one round trip instead of
another blind guess.
"""

import json

AUTH_COOKIE = "canvas_session"

# One source of truth for both halves of the flow. Capture (read_fields_js) and
# autofill MUST agree: when they drifted apart, a form the capture side missed
# was never saved, so autofill had nothing to fill and failed silently forever.
USER_SELECTORS = (
    "#username",
    "input[name=username]",
    "input[name=j_username]",
    "input[autocomplete=username]",
    "input[type=email]",
    "input[name*=user i]",
    "input[id*=user i]",
)
PASS_SELECTORS = (
    "#password",
    "input[name=password]",
    "input[name=j_password]",
    "input[autocomplete=current-password]",
    "input[type=password]",
)
SUBMIT_SELECTORS = (
    "input[type=submit]",
    "button[type=submit]",
    "#submit",
    "button[name=_eventId_proceed]",
    "input[name=_eventId_proceed]",
    "button[id*=login i]",
    "button[id*=submit i]",
)

# Retry budget for a form that mounts after load (fix #1 for #44).
RETRY_INTERVAL_MS = 250
RETRY_DEADLINE_MS = 6000
FRAME_DEPTH = 2
PROBE_INPUT_CAP = 40

EMPTY_RESULT = {"user": "", "pass": "", "submitted_password": False,
                "submitted_user": False, "frames": 0, "blocked_frames": 0,
                "tries": 0, "url": "", "done": False}


def collect_cookies(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Flatten (name, value) cookie pairs to a dict, last value winning."""
    out: dict[str, str] = {}
    for name, value in pairs:
        out[name] = value
    return out


def is_authenticated(names) -> bool:
    """The session is live once Canvas has set its httpOnly session cookie."""
    return AUTH_COOKIE in set(names)


def cookie_host(base_url: str) -> str:
    """The Canvas hostname from the configured base URL — 'utah.instructure.com'
    from 'https://utah.instructure.com'. Used to keep IdP/Duo cookies out of the
    jar we forward (and now persist) daemon-side."""
    host = str(base_url or "").split("://", 1)[-1]
    return host.split("/", 1)[0].split(":", 1)[0].strip().lower()


def is_canvas_cookie(domain: str, host: str) -> bool:
    """Cookie-domain match against the Canvas host, honouring the leading-dot
    wildcard form. An IdP cookie that happens to share a *name* with a Canvas one
    (`canvas_session` is not exotic) must never reach the daemon: we key the jar
    by name only, so a foreign one would both overwrite the real session and
    trip is_authenticated on its own."""
    d = str(domain or "").strip().lower().lstrip(".")
    host = str(host or "").strip().lower()
    if not d or not host:
        return False
    return host == d or host.endswith("." + d)


# --- shared JS building blocks ------------------------------------------------
# Same-origin frame traversal. A cross-origin frame is counted, never entered —
# `contentDocument` throws or returns null there, and that is a fact worth
# reporting rather than swallowing.
_DOCS_JS = """
  var __blocked = 0;
  function docs() {
    var out = [];
    function walk(doc, depth) {
      if (!doc || depth > %(depth)d) { return; }
      out.push(doc);
      var frames = [];
      try { frames = doc.querySelectorAll("iframe, frame"); } catch (e) { frames = []; }
      for (var i = 0; i < frames.length; i++) {
        var d = null;
        try { d = frames[i].contentDocument; } catch (e) { d = null; }
        if (d) { walk(d, depth + 1); } else { __blocked++; }
      }
    }
    walk(document, 0);
    return out;
  }
  function usable(el) {
    if (!el) { return false; }
    if (el.disabled || el.readOnly) { return false; }
    if (el.type === "hidden") { return false; }
    try {
      var r = el.getBoundingClientRect();
      if (r && r.width === 0 && r.height === 0) { return false; }
    } catch (e) { }
    return true;
  }
  function find(dlist, sels) {
    for (var i = 0; i < dlist.length; i++) {
      for (var j = 0; j < sels.length; j++) {
        var el = null;
        try { el = dlist[i].querySelector(sels[j]); } catch (e) { el = null; }
        if (el && usable(el)) { return {el: el, sel: sels[j]}; }
      }
    }
    return null;
  }
""" % {"depth": FRAME_DEPTH}


def read_fields_js() -> str:
    """JS expression that reads whatever uNID/password the user has typed into the
    CAS form, as JSON {"u":..,"p":..}. Polled from Python while the login view is
    open so a successful login can be saved to the keyring (capture across the
    multi-page CAS flow: each page contributes whichever field it shows). Shares
    USER_SELECTORS/PASS_SELECTORS and the frame walk with autofill_js, so the two
    can no longer target different forms. Never throws — returns "{}" on trouble.

    Note this re-reads a field Lumen itself autofilled, so a remembered login is
    simply re-saved with the same value it already had. Harmless: it just
    authenticated."""
    cfg = json.dumps({"userSel": list(USER_SELECTORS), "passSel": list(PASS_SELECTORS)})
    return ("(function() {\n  try {\n    var CFG = " + cfg + ";\n"
            + _DOCS_JS + """
    var dlist = docs();
    var u = find(dlist, CFG.userSel);
    var p = find(dlist, CFG.passSel);
    return JSON.stringify({u: u ? u.el.value : "", p: p ? p.el.value : ""});
  } catch (e) { return "{}"; }
})()""")


def autofill_js(unid: str, password: str, *,
                allow_password_submit: bool = False) -> str:
    """Best-effort fill of the U-of-U / CAS login form, returning a JSON report of
    what it did (selectors and counts only — never a value).

    Four things the original one-shot `el.value = x` got wrong, all of which
    produced the same silent nothing (#44):

    1. No retry. A form mounted by script after `loadFinished` was simply missed;
       we now poll on an interval AND watch for DOM mutations until a deadline.
    2. No events. A framework-controlled input ignores a raw `.value` write, so
       the field looked filled but the page's own state stayed empty and the
       submit sent blanks. We assign through the native property setter and
       dispatch bubbling `input`/`change`.
    3. No frames. CAS/Shibboleth pages commonly host the form in an iframe.
    4. No `input[type=password]` fallback when the id/name drifted.

    Injection is idempotent per document via a `window.__lumenAutofill` sentinel,
    so `loadFinished` firing twice cannot double-fill or double-submit. Because
    the retries are asynchronous, the string this returns is the FIRST attempt's
    report; poll `autofill_report_js()` for the settled one."""
    cfg = json.dumps({
        "u": unid, "p": password,
        "userSel": list(USER_SELECTORS), "passSel": list(PASS_SELECTORS),
        "submitSel": list(SUBMIT_SELECTORS),
        "allowPassSubmit": bool(allow_password_submit),
        "intervalMs": RETRY_INTERVAL_MS, "deadlineMs": RETRY_DEADLINE_MS,
    })
    return ("(function() {\n  try {\n    var CFG = " + cfg + ";\n"
            + _DOCS_JS + """
    var W = window;
    var prev = W.__lumenAutofill;
    if (prev && prev.report) {
      // Already running, already finished the password, or nothing new to add:
      // report what we have rather than filling a second time.
      if (!prev.report.done || prev.report.pass || !CFG.p) {
        return JSON.stringify(prev.report);
      }
      // Finished on a username-only page and we DO have a password: the SPA
      // swapped in step 2 without a navigation, so re-arm.
      try { if (prev.timer) { clearInterval(prev.timer); } } catch (e) { }
      try { if (prev.obs) { prev.obs.disconnect(); } } catch (e) { }
    }

    var report = {user: "", pass: "", submitted_password: false,
                  submitted_user: false, frames: 0, blocked_frames: 0,
                  tries: 0, url: String(location.href).split("?")[0],
                  done: false};
    var state = {report: report, timer: null, obs: null,
                 deadline: Date.now() + CFG.deadlineMs};
    W.__lumenAutofill = state;

    function setNative(el, v) {
      // The React/Vue-safe assignment: the native setter updates the DOM value
      // without the framework's own value property swallowing it, and the
      // bubbling events are what make the page believe a human typed.
      try {
        var d = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, "value");
        if (d && d.set) { d.set.call(el, v); } else { el.value = v; }
      } catch (e) { try { el.value = v; } catch (e2) { } }
      try { el.focus(); } catch (e) { }
      try {
        el.dispatchEvent(new Event("input", {bubbles: true}));
        el.dispatchEvent(new Event("change", {bubbles: true}));
      } catch (e) { }
      try { el.blur(); } catch (e) { }
    }

    function submit(dlist, el) {
      var btn = find(dlist, CFG.submitSel);
      if (btn) { try { btn.el.click(); return true; } catch (e) { } }
      try {
        if (el && el.form) { el.form.submit(); return true; }
      } catch (e) { }
      return false;
    }

    function attempt() {
      report.tries++;
      var dlist = docs();
      report.frames = dlist.length;
      report.blocked_frames = __blocked;
      var uf = CFG.u ? find(dlist, CFG.userSel) : null;
      var pf = CFG.p ? find(dlist, CFG.passSel) : null;
      if (!uf && !pf) { return false; }
      if (uf) { setNative(uf.el, CFG.u); report.user = uf.sel; }
      if (pf) {
        setNative(pf.el, CFG.p);
        report.pass = pf.sel;
        if (CFG.allowPassSubmit) {
          report.submitted_password = submit(dlist, pf.el);
        }
        return true;
      }
      // A username-only page (step 1 of the two-page CAS flow) always advances:
      // a bare uNID carries no lockout risk, and stalling here would strand the
      // password page we actually need to reach.
      report.submitted_user = submit(dlist, uf.el);
      return true;
    }

    function finish() {
      report.done = true;
      try { if (state.timer) { clearInterval(state.timer); } } catch (e) { }
      try { if (state.obs) { state.obs.disconnect(); } } catch (e) { }
      state.timer = null;
      state.obs = null;
    }

    function tick() {
      if (report.done) { return; }
      if (Date.now() > state.deadline) { finish(); return; }
      var hit = false;
      try { hit = attempt(); } catch (e) { hit = false; }
      if (hit) { finish(); }
    }

    tick();
    if (!report.done) {
      try { state.timer = setInterval(tick, CFG.intervalMs); } catch (e) { }
      try {
        state.obs = new MutationObserver(tick);
        state.obs.observe(document.documentElement,
                          {childList: true, subtree: true});
      } catch (e) { state.obs = null; }
    }
    return JSON.stringify(report);
  } catch (e) {
    return JSON.stringify({error: String(e && e.message || e)});
  }
})()""")


def autofill_report_js() -> str:
    """Read the settled report an earlier autofill_js injection left behind. The
    retries are async, so the injection's own return value is only attempt 1."""
    return """(function() {
  try {
    var s = window.__lumenAutofill;
    return s && s.report ? JSON.stringify(s.report) : "{}";
  } catch (e) { return "{}"; }
})()"""


def parse_autofill_result(raw) -> dict:
    """Total function: any shape of garbage collapses to EMPTY_RESULT. The caller
    is a Qt callback, where a raise is invisible and a wrong type is worse."""
    obj = raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            return dict(EMPTY_RESULT)
    if not isinstance(obj, dict):
        return dict(EMPTY_RESULT)
    out = dict(EMPTY_RESULT)
    for key in ("user", "pass", "url"):
        val = obj.get(key)
        out[key] = val if isinstance(val, str) else ""
    for key in ("submitted_password", "submitted_user", "done"):
        out[key] = bool(obj.get(key))
    for key in ("frames", "blocked_frames", "tries"):
        val = obj.get(key)
        out[key] = val if isinstance(val, int) and not isinstance(val, bool) else 0
    return out


def probe_js() -> str:
    """Diagnostic dump of every input the page exposes, across reachable frames:
    tag/id/name/type/autocomplete/placeholder/visibility/form-action. Deliberately
    NEVER reads `.value` — this output is meant to be pasted into a chat."""
    return ("(function() {\n  try {\n" + _DOCS_JS + """
    var dlist = docs();
    var out = [];
    for (var i = 0; i < dlist.length && out.length < %(cap)d; i++) {
      var els = [];
      try { els = dlist[i].querySelectorAll("input, select, textarea"); }
      catch (e) { els = []; }
      for (var j = 0; j < els.length && out.length < %(cap)d; j++) {
        var el = els[j];
        var rect = null;
        try { rect = el.getBoundingClientRect(); } catch (e) { rect = null; }
        out.push({
          frame: i,
          tag: String(el.tagName || "").toLowerCase(),
          id: el.id || "",
          name: el.name || "",
          type: el.type || "",
          autocomplete: el.getAttribute ? (el.getAttribute("autocomplete") || "") : "",
          placeholder: el.placeholder || "",
          visible: !!(rect && (rect.width > 0 || rect.height > 0)),
          disabled: !!el.disabled,
          form_action: (el.form && el.form.action) ? String(el.form.action) : ""
        });
      }
    }
    return JSON.stringify({url: String(location.href),
                           frames: dlist.length,
                           blocked_frames: __blocked,
                           inputs: out}, null, 1);
  } catch (e) { return JSON.stringify({error: String(e && e.message || e)}); }
})()""" % {"cap": PROBE_INPUT_CAP})


# --- auto-submit budget -------------------------------------------------------
ARMED = "armed"
SUBMITTED = "submitted"
FILL_ONLY = "fill_only"
DONE = "done"


class AutoSubmitPolicy:
    """The "fill and submit, but only the first time per session" rule, as a pure
    state machine so it can be tested as a truth table.

        ARMED --(submitted a password)--> SUBMITTED --(password field is
        back)--> FILL_ONLY (permanent)

    The FILL_ONLY edge is the safety property: a password field on screen *after*
    we already submitted one means that submit did not authenticate, and a second
    automatic attempt with the same wrong credentials walks straight into the
    university's account lockout. From then on Lumen fills and lets the human
    press the button.

    A username-only page never spends the budget, or the two-page CAS flow would
    burn it on step 1 and never auto-submit the password at all."""

    def __init__(self):
        self.state = ARMED

    @property
    def allow_password_submit(self) -> bool:
        return self.state == ARMED

    def record(self, result: dict) -> str:
        result = result or {}
        if self.state in (FILL_ONLY, DONE):
            return self.state
        if self.state == SUBMITTED:
            if result.get("pass"):
                self.state = FILL_ONLY
        elif result.get("submitted_password"):
            self.state = SUBMITTED
        return self.state

    def done(self) -> None:
        """The auth cookie arrived — stop injecting anything at all."""
        self.state = DONE

    def reset(self) -> None:
        """A fresh login attempt (Connect / Disconnect) re-arms the budget."""
        self.state = ARMED
