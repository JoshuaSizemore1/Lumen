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
# The deadline was 6 s, and #57 established that a Canvas page can take far
# longer than that to become interactive — so the retry loop was routinely dead
# before the form existed. A minute of a 250 ms poll on a login page the user is
# looking at is not a power concern, and the MutationObserver beside it is
# event-driven and free.
RETRY_INTERVAL_MS = 250
RETRY_DEADLINE_MS = 60000
FRAME_DEPTH = 2
PROBE_INPUT_CAP = 40

EMPTY_RESULT = {"user": "", "pass": "", "submitted_password": False,
                "submitted_user": False, "frames": 0, "blocked_frames": 0,
                "tries": 0, "url": "", "user_fields": 0, "pass_fields": 0,
                "done": False}


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
  // Every usable match, not just the first (#44c). The old `find` stopped at
  // the first password input it saw, so a hidden or decoy field on step 1 of
  // the CAS flow consumed the whole budget on the wrong element.
  function findAll(dlist, sels) {
    var out = [], seen = [];
    for (var i = 0; i < dlist.length; i++) {
      for (var j = 0; j < sels.length; j++) {
        var els = [];
        try { els = dlist[i].querySelectorAll(sels[j]); } catch (e) { els = []; }
        for (var k = 0; k < els.length; k++) {
          if (!usable(els[k])) { continue; }
          if (seen.indexOf(els[k]) >= 0) { continue; }
          seen.push(els[k]);
          out.push({el: els[k], sel: sels[j]});
        }
      }
    }
    return out;
  }
""" % {"depth": FRAME_DEPTH}

# Kept OUT of _DOCS_JS: probe_js shares that block and must provably never
# contain the string `.value` — it is the one diagnostic safe to paste into a
# chat, and the test that guarantees it is a substring check on the source.
_SETTER_JS = """
  function setNative(el, v) {
    // The React/Vue-safe assignment: the native setter updates the DOM without
    // the framework's own value property swallowing it, and the bubbling
    // events are what make the page believe a human typed.
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
  }
"""


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
            + _DOCS_JS + _SETTER_JS + """
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
                  user_fields: 0, pass_fields: 0, done: false};
    var state = {report: report, timer: null, obs: null,
                 deadline: Date.now() + CFG.deadlineMs};
    W.__lumenAutofill = state;

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
      // Every candidate, not the first (#44c). A decoy or off-screen password
      // input used to swallow the whole attempt.
      var ufs = CFG.u ? findAll(dlist, CFG.userSel) : [];
      var pfs = CFG.p ? findAll(dlist, CFG.passSel) : [];
      report.user_fields = ufs.length;
      report.pass_fields = pfs.length;
      if (!ufs.length && !pfs.length) { return false; }
      for (var a = 0; a < ufs.length; a++) { setNative(ufs[a].el, CFG.u); }
      if (ufs.length) { report.user = ufs[0].sel; }
      if (pfs.length) {
        for (var b = 0; b < pfs.length; b++) { setNative(pfs[b].el, CFG.p); }
        report.pass = pfs[0].sel;
        if (CFG.allowPassSubmit) {
          report.submitted_password = submit(dlist, pfs[0].el);
        }
        return true;
      }
      var uf = ufs[0];
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


def login_form_present_js() -> str:
    """Is *this page* a login form? JSON
    {"login": bool, "user": n, "pass": n, "frames": n, "blocked": n}.

    This replaces "did the user arrive via the Connect button" as the gate on
    autofill (#44b). Browse lands on the CAS form whenever the session has
    expired — which is exactly when a saved login is most useful — and the old
    gate meant Lumen would not even try.

    `blocked` is reported rather than swallowed: a form inside a cross-origin
    frame is the one case no amount of code will fix, and the user deserves to
    be told to type it rather than watch nothing happen."""
    cfg = json.dumps({"userSel": list(USER_SELECTORS),
                      "passSel": list(PASS_SELECTORS)})
    return ("(function() {\n  try {\n    var CFG = " + cfg + ";\n"
            + _DOCS_JS + """
    var dlist = docs();
    var u = findAll(dlist, CFG.userSel).length;
    var p = findAll(dlist, CFG.passSel).length;
    return JSON.stringify({login: !!(u || p), user: u, pass: p,
                           frames: dlist.length, blocked: __blocked});
  } catch (e) { return JSON.stringify({login: false, user: 0, pass: 0,
                                       frames: 0, blocked: 0}); }
})()""")


def fill_on_focus_js(unid: str, password: str) -> str:
    """Install Chrome's actual behaviour: fill a login field when it takes focus.

    This is the structural answer to every timing problem in #44. Load
    deadlines, forms mounted by script thirty seconds late, a two-page CAS flow,
    an SPA that swaps step 2 in without navigating — none of them matter if the
    trigger is the user putting the cursor in the box. Idempotent per document.

    Only fills an EMPTY field, so it can never overwrite something the user is
    part-way through typing, and never submits anything."""
    cfg = json.dumps({"u": unid, "p": password,
                      "userSel": list(USER_SELECTORS),
                      "passSel": list(PASS_SELECTORS)})
    return ("(function() {\n  try {\n    var CFG = " + cfg + ";\n"
            + _DOCS_JS + _SETTER_JS + """
    function is(el, sels) {
      for (var i = 0; i < sels.length; i++) {
        try { if (el.matches && el.matches(sels[i])) { return true; } }
        catch (e) { }
      }
      return false;
    }
    function onFocus(ev) {
      var el = ev && (ev.target || ev.srcElement);
      if (!el || !usable(el)) { return; }
      try { if (el.value) { return; } } catch (e) { return; }
      if (CFG.p && is(el, CFG.passSel)) { setNative(el, CFG.p); return; }
      if (CFG.u && is(el, CFG.userSel)) { setNative(el, CFG.u); }
    }
    var dlist = docs();
    var armed = 0;
    for (var i = 0; i < dlist.length; i++) {
      var d = dlist[i];
      try {
        if (d.__lumenFocusFill) { continue; }
        d.addEventListener("focusin", onFocus, true);
        d.__lumenFocusFill = true;
        armed++;
      } catch (e) { }
    }
    return JSON.stringify({armed: armed, frames: dlist.length,
                           blocked: __blocked});
  } catch (e) { return JSON.stringify({armed: 0, frames: 0, blocked: 0}); }
})()""")


def capture_on_submit_js() -> str:
    """Read the login fields at the instant the form is submitted, not once a
    second (#44a).

    The 1 Hz sampler missed a password typed and submitted inside the same
    second, and missed a pasted one entirely — which is very likely why the
    keyring was empty the whole time, and why nothing was ever logged about it.
    Values are stashed on `window.__lumenCapture` for `read_capture_js` to
    collect; they never leave the page on their own."""
    cfg = json.dumps({"userSel": list(USER_SELECTORS),
                      "passSel": list(PASS_SELECTORS)})
    return ("(function() {\n  try {\n    var CFG = " + cfg + ";\n"
            + _DOCS_JS + """
    var W = window;
    if (!W.__lumenCapture) { W.__lumenCapture = {u: "", p: ""}; }
    function grab() {
      try {
        var dlist = docs();
        var u = find(dlist, CFG.userSel);
        var p = find(dlist, CFG.passSel);
        if (u && u.el.value) { W.__lumenCapture.u = u.el.value; }
        if (p && p.el.value) { W.__lumenCapture.p = p.el.value; }
      } catch (e) { }
    }
    var dlist = docs();
    var armed = 0;
    for (var i = 0; i < dlist.length; i++) {
      var d = dlist[i];
      try {
        if (d.__lumenCaptureArmed) { continue; }
        // Every way a login form can be sent: the form's own submit event, a
        // click anywhere (the button may be a div), and Enter in a field.
        d.addEventListener("submit", grab, true);
        d.addEventListener("click", grab, true);
        d.addEventListener("keydown", function (ev) {
          if (ev && (ev.key === "Enter" || ev.keyCode === 13)) { grab(); }
        }, true);
        // And on the way out, for a form that navigates without any of those.
        try { d.addEventListener("beforeunload", grab, true); } catch (e) { }
        d.__lumenCaptureArmed = true;
        armed++;
      } catch (e) { }
    }
    return JSON.stringify({armed: armed});
  } catch (e) { return JSON.stringify({armed: 0}); }
})()""")


def read_capture_js() -> str:
    """Whatever capture_on_submit_js stashed, plus whatever is in the fields
    right now — the live fields cover a single-page form that never submitted
    while the stash covers a multi-page CAS flow whose earlier page is gone."""
    cfg = json.dumps({"userSel": list(USER_SELECTORS),
                      "passSel": list(PASS_SELECTORS)})
    return ("(function() {\n  try {\n    var CFG = " + cfg + ";\n"
            + _DOCS_JS + """
    var stash = window.__lumenCapture || {u: "", p: ""};
    var dlist = docs();
    var u = find(dlist, CFG.userSel);
    var p = find(dlist, CFG.passSel);
    return JSON.stringify({u: (u && u.el.value) || stash.u || "",
                           p: (p && p.el.value) || stash.p || ""});
  } catch (e) { return "{}"; }
})()""")


def parse_form_state(raw) -> dict:
    """Total function over login_form_present_js' output."""
    obj = raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            obj = None
    if not isinstance(obj, dict):
        return {"login": False, "user": 0, "pass": 0, "frames": 0, "blocked": 0}
    def num(key):
        v = obj.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) else 0
    return {"login": bool(obj.get("login")), "user": num("user"),
            "pass": num("pass"), "frames": num("frames"),
            "blocked": num("blocked")}


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
    for key in ("frames", "blocked_frames", "tries",
                "user_fields", "pass_fields"):
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
