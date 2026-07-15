# Phase 10 — UI Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the real UI gaps for Phase 10 — make Settings show live config, give Chat an empty state, normalize empty/loading/error states across screens, and audit keyboard nav — without chasing pixel parity.

**Architecture:** One new read-only daemon request (`settings.get`) backed by a pure `build_settings_snapshot(cfg)` function; the Settings screen renders that snapshot instead of hardcoded fixtures. UI-only changes for the Chat empty state, a shared `empty_state()` placeholder widget, and per-screen state normalization. No daemon business-logic changes beyond the read-only endpoint.

**Tech Stack:** Python 3.12, asyncio daemon, PyQt6 (`lumen/ui_v2`), pytest + pytest-qt. Run tests with `.venv/bin/python -m pytest -q` (or `uv run pytest -q`).

## Global Constraints

- Idle-unload is non-negotiable: `settings.get` is a pure read of the already-loaded `Config` — it must not touch Ollama, load a model, or poll anything.
- No silent writes: `settings.get` is read-only; it changes nothing. Settings toggles are reflect-only (edit-in-file model).
- UI holds no business logic: screens render the snapshot and send requests; all derivation lives in `build_settings_snapshot`.
- The shipped UI is `lumen/ui_v2` (`lumen-ui = lumen.ui_v2.app:main`). Do NOT edit the legacy `lumen/ui/` tree.
- Accent is out of scope: it already persists via `QSettings` (`main.py:224/253`) and is not in the snapshot.
- Commit convention (CLAUDE.md): every commit message ends with `This commit used N prompts.` and carries NO `Co-Authored-By` trailer. For this plan's tasks, use the running session prompt count for N.

---

### Task 1: `build_settings_snapshot(cfg)` — pure config→display function

**Files:**
- Create: `lumen/daemon/settings_snapshot.py`
- Test: `tests/daemon/test_settings_snapshot.py`

**Interfaces:**
- Consumes: `Config` (from `lumen.daemon.config`), `google_auth.connected`, `llm.client.NUM_CTX`.
- Produces: `build_settings_snapshot(cfg: Config) -> dict` with keys `model`, `sync`, `accounts`, `mcp`, `paths` (exact shape in the spec, minus `appearance`).

- [ ] **Step 1: Write the failing test**

```python
# tests/daemon/test_settings_snapshot.py
from lumen.daemon.config import Config, MCPConfig, MCPServerConfig, SyncConfig
from lumen.daemon.settings_snapshot import build_settings_snapshot


def test_snapshot_reports_model_and_sync_from_config():
    cfg = Config(model="qwen3:4b-instruct", idle_unload_minutes=10,
                 sync=SyncConfig(gmail_poll_minutes=5, calendar_poll_minutes=5))
    snap = build_settings_snapshot(cfg)
    assert snap["model"]["name"] == "qwen3:4b-instruct"
    assert snap["model"]["runtime"] == "ollama"
    assert snap["model"]["num_ctx"] == 8192
    assert snap["model"]["idle_unload_minutes"] == 10
    assert snap["sync"]["gmail_poll_minutes"] == 5
    assert snap["sync"]["calendar_poll_minutes"] == 5


def test_snapshot_accounts_not_connected_by_default(tmp_path):
    # A Config whose google token path does not exist reports both disconnected.
    from lumen.daemon.config import GoogleConfig
    cfg = Config(google=GoogleConfig(token_path=tmp_path / "nope.json"))
    snap = build_settings_snapshot(cfg)
    assert snap["accounts"]["gmail"]["connected"] is False
    assert snap["accounts"]["google_calendar"]["connected"] is False


def test_snapshot_lists_configured_mcp_servers():
    cfg = Config(mcp=MCPConfig(enabled=True, servers=(
        MCPServerConfig(name="search", command="npx", args=("brave-search",)),
    )))
    snap = build_settings_snapshot(cfg)
    assert snap["mcp"]["enabled"] is True
    assert snap["mcp"]["servers"][0]["name"] == "search"
    assert snap["mcp"]["servers"][0]["enabled"] is True
    assert "npx" in snap["mcp"]["servers"][0]["detail"]


def test_snapshot_paths_use_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config(db_path=tmp_path / "share" / "lumen.db",
                 memory_path=tmp_path / "share" / "memory.md")
    snap = build_settings_snapshot(cfg)
    assert snap["paths"]["db"].startswith("~/")
    assert snap["paths"]["memory"].startswith("~/")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/test_settings_snapshot.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'lumen.daemon.settings_snapshot'`

- [ ] **Step 3: Write minimal implementation**

```python
# lumen/daemon/settings_snapshot.py
"""Read-only snapshot of the loaded Config for the Settings screen (Phase 10).

Pure: derives display values and live account-connection status from an
already-loaded Config. Never touches Ollama, never writes anything."""
from pathlib import Path

from .connectors import google_auth
from .llm.client import NUM_CTX


def _tilde(path) -> str:
    s = str(path)
    home = str(Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s


def _server_detail(server) -> str:
    parts = [server.command, *server.args]
    detail = " ".join(str(p) for p in parts)
    return detail if len(detail) <= 42 else detail[:39] + "…"


def build_settings_snapshot(cfg) -> dict:
    return {
        "model": {
            "runtime": "ollama",
            "name": cfg.model,
            "escalation_model": cfg.escalation_model,
            "num_ctx": NUM_CTX,
            "idle_unload_minutes": cfg.idle_unload_minutes,
            "ollama_url": cfg.ollama_url,
        },
        "sync": {
            "gmail_poll_minutes": cfg.sync.gmail_poll_minutes,
            "calendar_poll_minutes": cfg.sync.calendar_poll_minutes,
            "gmail_window_months": cfg.sync.gmail_window_months,
            "calendar_window_past_days": cfg.sync.calendar_window_past_days,
            "calendar_window_future_days": cfg.sync.calendar_window_future_days,
        },
        "accounts": {
            "gmail": {"connected": google_auth.connected(
                cfg.google, google_auth.GMAIL_READ_SCOPES)},
            "google_calendar": {"connected": google_auth.connected(
                cfg.google, google_auth.READ_SCOPES)},
        },
        "mcp": {
            "enabled": cfg.mcp.enabled,
            "servers": [
                {"name": s.name, "command": s.command,
                 "detail": _server_detail(s), "enabled": cfg.mcp.enabled}
                for s in cfg.mcp.servers
            ],
        },
        "paths": {
            "db": _tilde(cfg.db_path),
            "memory": _tilde(cfg.memory_path),
        },
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/test_settings_snapshot.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/settings_snapshot.py tests/daemon/test_settings_snapshot.py
git commit -m "Add read-only settings snapshot for the Settings screen

This commit used N prompts."
```

---

### Task 2: Router `settings.get` handler + wire `config` into Router

**Files:**
- Modify: `lumen/daemon/router.py` (add `config=None` kwarg in `__init__` at ~line 329; add `elif type_ == "settings.get"` in `handle`, near the other one-shots ~line 479)
- Modify: `lumen/daemon/__main__.py:52-68` (pass `config=cfg` to `Router(...)`)
- Test: `tests/daemon/test_settings_router.py`

**Interfaces:**
- Consumes: `build_settings_snapshot` (Task 1); `Router.__init__` gains `config=None`.
- Produces: request `settings.get` with empty payload yields `{"result": <snapshot dict>}`; yields `{"error": "settings unavailable"}` when no config was provided.

- [ ] **Step 1: Write the failing test**

```python
# tests/daemon/test_settings_router.py
from lumen.daemon.config import Config
from lumen.daemon.router import Router
from tests.daemon.test_router import FakeLLM, FakeStore, collect


async def test_settings_get_returns_snapshot():
    r = Router(FakeLLM(), FakeStore(), config=Config(model="qwen3:4b-instruct"))
    out = await collect(r, "settings.get", {})
    assert out[0]["result"]["model"]["name"] == "qwen3:4b-instruct"
    assert "accounts" in out[0]["result"] and "sync" in out[0]["result"]


async def test_settings_get_without_config_errs():
    r = Router(FakeLLM(), FakeStore())
    out = await collect(r, "settings.get", {})
    assert "error" in out[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/test_settings_router.py -q`
Expected: FAIL — `Router.__init__` has no `config` kwarg (TypeError) / no `settings.get` branch.

- [ ] **Step 3: Add the `config` kwarg**

In `lumen/daemon/router.py`, extend the `__init__` signature (add `config=None` to the keyword-only group ending at line 329) and store it:

```python
                 procedures=None, distill_trigger=None,
                 config=None,
                 max_iterations=4):
```

Immediately after `self._max_iterations = max_iterations` (line 351), add:

```python
        self._config = config    # loaded Config for the read-only settings.get
```

- [ ] **Step 4: Add the handler**

In `handle`, after the `elif type_ == "manabi.status":` block (ends ~line 483), add:

```python
        elif type_ == "settings.get":
            if self._config is None:
                yield {"error": "settings unavailable"}
            else:
                from .settings_snapshot import build_settings_snapshot
                yield {"result": build_settings_snapshot(self._config)}
```

- [ ] **Step 5: Wire it from `__main__.py`**

In `lumen/daemon/__main__.py`, add `config=cfg,` to the `Router(...)` call (e.g. right before `max_iterations=cfg.mcp.max_iterations)` at line 68):

```python
                    distill_trigger=memory_worker.schedule,
                    config=cfg,
                    max_iterations=cfg.mcp.max_iterations)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/daemon/test_settings_router.py -q`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add lumen/daemon/router.py lumen/daemon/__main__.py tests/daemon/test_settings_router.py
git commit -m "Add read-only settings.get daemon request

This commit used N prompts."
```

---

### Task 3: `empty_state()` shared placeholder widget

**Files:**
- Modify: `lumen/ui_v2/widgets.py` (add `empty_state` near the other factories, after `scroll` ~line 120)
- Test: `tests/ui/test_empty_state.py`

**Interfaces:**
- Consumes: existing `label`, `vbox` from `widgets.py`; theme colors `T.TEXT_DIM`, `T.TEXT_FAINT`.
- Produces: `empty_state(text: str, sub: str | None = None) -> QWidget` — a centered, muted placeholder.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_empty_state.py
from PyQt6.QtWidgets import QLabel
from lumen.ui_v2.widgets import empty_state


def _texts(w):
    return " | ".join(l.text() for l in w.findChildren(QLabel))


def test_empty_state_shows_text_and_sub(qtbot):
    w = empty_state("No messages yet", "your inbox is clear")
    qtbot.addWidget(w)
    t = _texts(w)
    assert "No messages yet" in t and "your inbox is clear" in t


def test_empty_state_text_only(qtbot):
    w = empty_state("Nothing here")
    qtbot.addWidget(w)
    assert "Nothing here" in _texts(w)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ui/test_empty_state.py -q`
Expected: FAIL — `ImportError: cannot import name 'empty_state'`.

- [ ] **Step 3: Implement the helper**

Add to `lumen/ui_v2/widgets.py` (after `scroll`):

```python
def empty_state(text: str, sub: str | None = None) -> QWidget:
    """Centered muted placeholder for offline/loading/empty/error panes.
    One helper so wording and styling stay consistent across screens."""
    from PyQt6.QtCore import Qt
    w = QWidget()
    lay = vbox(w, (0, 0, 0, 0), 4)
    lay.addStretch(1)
    title = label(text, 13, T.TEXT_DIM)
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(title)
    if sub:
        s = label(sub, 11, T.TEXT_FAINT)
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(s)
    lay.addStretch(1)
    return w
```

Confirm `QWidget` is already imported in `widgets.py` (it is — used throughout). If `Qt` is imported at module top, use that instead of the local import.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ui/test_empty_state.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add lumen/ui_v2/widgets.py tests/ui/test_empty_state.py
git commit -m "Add shared empty_state placeholder widget

This commit used N prompts."
```

---

### Task 4: Settings screen renders the live snapshot

**Files:**
- Modify: `lumen/ui_v2/screens/settings.py` (rework `__init__` to build from a snapshot; add `_populate(snap)`, `_status_row(...)`; drop `self.accounts`/`self.mcp` stubs and `_set`)
- Modify: `lumen/ui_v2/state.py` (add `fetch_settings(cb)` mirroring existing `self._data.request(...)` methods)
- Test: `tests/ui/test_settings_live.py`

**Interfaces:**
- Consumes: `AppState.fetch_settings(cb)` → calls back with the `settings.get` result dict; `empty_state` (Task 3); `Dot`, `label`, `hline` from `widgets`.
- Produces: a Settings screen whose account/model/sync rows reflect the snapshot; account/MCP rows are reflect-only status indicators (no `Switch`).

- [ ] **Step 1: Add `fetch_settings` to AppState**

In `lumen/ui_v2/state.py`, near the other one-shot request methods (e.g. by `refresh_procedures`), add:

```python
    def fetch_settings(self, cb) -> None:
        """One-shot read of the live daemon config for the Settings screen."""
        self._data.request("settings.get", {}, cb)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/ui/test_settings_live.py
from PyQt6.QtWidgets import QLabel
from tests.ui.test_ui_v2 import FakeClient
from lumen.ui_v2.state import AppState
from lumen.ui_v2.screens.settings import SettingsScreen

SNAP = {
    "model": {"runtime": "ollama", "name": "qwen3:4b-instruct",
              "escalation_model": None, "num_ctx": 8192,
              "idle_unload_minutes": 10, "ollama_url": "http://127.0.0.1:11434"},
    "sync": {"gmail_poll_minutes": 5, "calendar_poll_minutes": 5,
             "gmail_window_months": 6, "calendar_window_past_days": 30,
             "calendar_window_future_days": 60},
    "accounts": {"gmail": {"connected": True},
                 "google_calendar": {"connected": False}},
    "mcp": {"enabled": True, "servers": [
        {"name": "search", "command": "npx", "detail": "npx brave-search",
         "enabled": True}]},
    "paths": {"db": "~/x/lumen.db", "memory": "~/x/memory.md"},
}


def _texts(w):
    return " | ".join(l.text() for l in w.findChildren(QLabel))


def _state():
    data, chat, confirm = FakeClient(), FakeClient(), FakeClient()
    return AppState(data=data, chat=chat, confirm=confirm), data


def test_settings_populates_from_snapshot(qtbot):
    state, data = _state()
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w.show()                       # triggers showEvent → fetch_settings
    data.cb_for("settings.get")(SNAP)   # deliver the fake snapshot
    t = _texts(w)
    assert "qwen3:4b-instruct" in t          # real model, not the old llama3.1:8b
    assert "search" in t                     # real mcp server name
    assert "llama3.1:8b" not in t            # fixture is gone
    assert "run: lumen-google-auth" in t     # calendar not connected → hint shown
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ui/test_settings_live.py -q`
Expected: FAIL — screen still renders hardcoded `llama3.1:8b`, no `settings.get` request, assertion fails.

- [ ] **Step 4: Rework `settings.py` to build from the snapshot**

Replace the hardcoded body. Key changes (keep the accent + memory sections exactly as they already are):

1. In `__init__`, drop `self.accounts`/`self.mcp` stub dicts and the `_set` method. Keep references to the container layout so `_populate` can fill `[accounts]`, `[mcp_servers]`, `[model]`, `[sync]` sections. Build the static scaffold (section headers, accent, memory), leave the dynamic rows to `_populate`.

2. Add a reflect-only status row helper (replaces `_toggle_row`'s Switch):

```python
    def _status_row(self, name: str, detail: str, state_text: str,
                    ok: bool, hint: str | None = None) -> QWidget:
        row = QWidget()
        rl = hbox(row, (12, 9, 12, 9), 12)
        n = label(name, 12, T.TEXT_SECONDARY)
        n.setFixedWidth(150)
        rl.addWidget(n)
        rl.addWidget(label(detail, 11, T.TEXT_DIM), 1)
        if hint:
            rl.addWidget(label(hint, 10, T.TEXT_FAINT))
        rl.addWidget(Dot(7, T.OK if ok else T.TEXT_GHOST))
        rl.addWidget(label(state_text, 10, T.OK if ok else T.TEXT_MUTED))
        return row
```

3. Add `_populate(self, snap)` that clears the dynamic containers and fills them:

```python
    def _populate(self, snap: dict):
        acc = snap["accounts"]
        clear_layout(self._accounts_box.layout())
        self._accounts_box.layout().addWidget(self._status_row(
            "gmail", "read + modify", "connected" if acc["gmail"]["connected"]
            else "not connected", acc["gmail"]["connected"],
            None if acc["gmail"]["connected"] else "run: lumen-google-auth"))
        self._accounts_box.layout().addWidget(self._status_row(
            "google_calendar", "read + write",
            "connected" if acc["google_calendar"]["connected"] else "not connected",
            acc["google_calendar"]["connected"],
            None if acc["google_calendar"]["connected"] else "run: lumen-google-auth"))

        clear_layout(self._mcp_box.layout())
        if snap["mcp"]["servers"]:
            for s in snap["mcp"]["servers"]:
                self._mcp_box.layout().addWidget(self._status_row(
                    s["name"], s["detail"],
                    "enabled" if s["enabled"] else "off", s["enabled"]))
        else:
            self._mcp_box.layout().addWidget(
                label("no MCP servers configured", 11, T.TEXT_FAINT))

        m, sy = snap["model"], snap["sync"]
        clear_layout(self._model_box.layout())
        for line in (
            _config_line("runtime", f'"{m["runtime"]}"', T.OK),
            _config_line("name", f'"{m["name"]}"', T.OK),
            _config_line("context", str(m["num_ctx"]), T.INFO),
            _config_line("idle_timeout", str(m["idle_unload_minutes"]), T.INFO,
                         "minutes"),
        ):
            self._model_box.layout().addWidget(line)
        clear_layout(self._sync_box.layout())
        for line in (
            _config_line("gmail_poll", str(sy["gmail_poll_minutes"]), T.INFO, "min"),
            _config_line("calendar_poll", str(sy["calendar_poll_minutes"]), T.INFO, "min"),
            _config_line("gmail_window", str(sy["gmail_window_months"]), T.INFO, "months"),
            _config_line("db", f'"{snap["paths"]["db"]}"', T.OK),
        ):
            self._sync_box.layout().addWidget(line)
```

   Where `self._accounts_box`, `self._mcp_box`, `self._model_box`, `self._sync_box` are `QWidget`s with a `vbox` layout, created in `__init__` under each section header and added to the column. Seed each with a muted "loading…" `label` so the pre-snapshot state is not blank.

4. In `showEvent`, request the snapshot (in addition to the existing `refresh_procedures`):

```python
    def showEvent(self, ev):
        super().showEvent(ev)
        self.state.refresh_procedures()
        self.state.fetch_settings(self._on_settings)

    def _on_settings(self, result: dict):
        if not isinstance(result, dict) or "model" not in result:
            return
        self._populate(result)
```

5. Update imports at the top of `settings.py`: add `Dot` to the `from ..widgets import (...)` line; `Switch` may be dropped if no longer used.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ui/test_settings_live.py -q`
Expected: PASS

- [ ] **Step 6: Run the full UI suite for regressions**

Run: `.venv/bin/python -m pytest tests/ui -q`
Expected: PASS (fix any test that asserted old fixture strings; update those assertions to match live behavior).

- [ ] **Step 7: Commit**

```bash
git add lumen/ui_v2/screens/settings.py lumen/ui_v2/state.py tests/ui/test_settings_live.py
git commit -m "Render live config in the Settings screen

This commit used N prompts."
```

---

### Task 5: Chat empty state with clickable example prompts

**Files:**
- Modify: `lumen/ui_v2/screens/chat.py` (add an empty-state widget shown when the thread has no turns; show it in `_build_main`, `new_chat`, and after `_render_thread` when empty)
- Test: `tests/ui/test_chat_empty.py`

**Interfaces:**
- Consumes: `label`, `vbox`, `ClickLabel`/`ClickRow` from `widgets`; the screen's existing `_submit`/`input` path.
- Produces: an empty state that renders identity + example prompts; clicking a prompt submits it as the first message.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_chat_empty.py
from PyQt6.QtWidgets import QLabel
from tests.ui.test_ui_v2 import FakeClient
from lumen.ui_v2.state import AppState
from lumen.ui_v2.screens.chat import ChatScreen, EXAMPLE_PROMPTS


def _texts(w):
    return " | ".join(l.text() for l in w.findChildren(QLabel))


def _screen(qtbot):
    data, chat, confirm = FakeClient(), FakeClient(), FakeClient()
    state = AppState(data=data, chat=chat, confirm=confirm)
    w = ChatScreen(state, chat)
    qtbot.addWidget(w)
    return w, chat


def test_chat_shows_empty_state_and_examples(qtbot):
    w, _ = _screen(qtbot)
    t = _texts(w)
    assert "Lumen" in t
    assert EXAMPLE_PROMPTS[0] in t


def test_clicking_example_submits_it(qtbot):
    w, chat = _screen(qtbot)
    w._submit_text(EXAMPLE_PROMPTS[0])   # simulate the prompt click
    assert any(t == "chat" and p.get("message") == EXAMPLE_PROMPTS[0]
               for t, p in chat.sent) or \
           any(t == "chat" for t, _p, _cb in chat.requests)
```

Adjust the second assertion to match how `chat.py` actually sends a message (it may use `send` or `request`); inspect `_submit` first and mirror its call.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ui/test_chat_empty.py -q`
Expected: FAIL — `EXAMPLE_PROMPTS`/empty state not present.

- [ ] **Step 3: Implement the empty state**

In `lumen/ui_v2/screens/chat.py`:

```python
EXAMPLE_PROMPTS = [
    "what's on my calendar today",
    "summarize unread from Priya",
    "recommend a book like my last two",
]
```

Add a builder and a text-submit seam (refactor `_submit` to route through `_submit_text`):

```python
    def _build_empty_state(self) -> QWidget:
        from PyQt6.QtCore import Qt
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 6)
        v.addStretch(1)
        head = label("❯ Lumen", 20, T.TEXT_PRIMARY, 600)
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = label("local · private · on-device", 11, T.TEXT_DIM)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(head); v.addWidget(sub)
        v.addSpacing(16)
        tryl = label("Try asking:", 10, T.TEXT_FAINT, ls=1)
        tryl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(tryl)
        for prompt in EXAMPLE_PROMPTS:
            row = ClickLabel(f"❯  {prompt}", 12, T.TEXT_SECONDARY)
            row.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.clicked.connect(lambda _=False, p=prompt: self._submit_text(p))
            v.addWidget(row)
        v.addStretch(1)
        return w
```

Wire it: keep a `self._empty` widget in the thread pane, shown when there are no turns and hidden once a turn renders; `new_chat()` shows it again. `_submit_text(text)` sets the input text and runs the existing submit logic; `_submit` becomes `self._submit_text(self.input.text())`. Match `ClickLabel`'s actual constructor signature in `widgets.py` (check `class ClickLabel` — adapt the args if it differs).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ui/test_chat_empty.py -q`
Expected: PASS

- [ ] **Step 5: Run the full UI suite**

Run: `.venv/bin/python -m pytest tests/ui -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lumen/ui_v2/screens/chat.py tests/ui/test_chat_empty.py
git commit -m "Add Chat empty state with clickable example prompts

This commit used N prompts."
```

---

### Task 6: Normalize empty / error states across the remaining screens

**Files:**
- Modify: `lumen/ui_v2/screens/dashboard.py`, `calendar.py`, `mail.py`, `todos.py`, `books.py`
- Test: `tests/ui/test_empty_states.py`

**Interfaces:**
- Consumes: `empty_state` (Task 3).
- Produces: each screen shows a sensible placeholder for its empty case (and offline where the daemon is unreachable) instead of a blank pane.

For each screen, find where live rows are rendered and add an empty branch:
- **Dashboard:** when there are no todos / no events / no unread, show `empty_state("Nothing due today")` / `"No events today"` / `"Inbox clear"` in the respective column instead of an empty box.
- **Calendar:** keep `NOT_CONNECTED`; route it and an empty-month case through `empty_state` for consistent styling.
- **Mail:** migrate the existing "Gmail not connected" / "No message selected" strings to `empty_state`; add an empty-inbox placeholder when the list is empty.
- **Todos:** when there are zero open todos across all groups, show `empty_state("All clear", "no open todos")`.
- **Books:** when the catalog is empty show `empty_state("No books logged yet")`; when there are no recs yet keep the existing "grounded in your reading log" footer but show a muted "ask for a suggestion" placeholder in the recs column.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_empty_states.py
from PyQt6.QtWidgets import QLabel
from tests.ui.test_ui_v2 import FakeClient
from lumen.ui_v2.state import AppState


def _texts(w):
    return " | ".join(l.text() for l in w.findChildren(QLabel))


def _state():
    return AppState(data=FakeClient(), chat=FakeClient(), confirm=FakeClient())


def test_todos_all_clear_when_empty(qtbot):
    from lumen.ui_v2.screens.todos import TodosScreen
    w = TodosScreen(_state())
    qtbot.addWidget(w)
    w.set_todos([])            # match the screen's real setter name/signature
    assert "All clear" in _texts(w)


def test_books_empty_catalog(qtbot):
    from lumen.ui_v2.screens.books import BooksScreen
    w = BooksScreen(_state())
    qtbot.addWidget(w)
    w.set_books([])            # match the screen's real setter name/signature
    assert "No books logged yet" in _texts(w)
```

Before writing, open each screen and confirm the exact setter method names and signatures (e.g. `set_todos`, `_set_books`); adjust the test calls to match. If a screen builds rows in `__init__` from `sample_data`, add an explicit setter or reuse the state signal handler the screen already connects to.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ui/test_empty_states.py -q`
Expected: FAIL — no "All clear" / "No books logged yet" placeholder yet.

- [ ] **Step 3: Implement empty branches**

Add the `empty_state(...)` branches described above in each screen's row-building method. Import `empty_state` from `..widgets`. Keep existing not-connected copy where it is already good; wrap it in `empty_state` only for styling consistency.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ui/test_empty_states.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + screenshots**

Run: `.venv/bin/python -m pytest -q`
Then: `QT_QPA_PLATFORM=offscreen uv run python scripts/screenshot.py lumen.ui_v2.main:build_window /tmp/phase10_shots/ --size 1320x798` and eyeball each screen.
Expected: suite PASS; screenshots show placeholders, not blank panes.

- [ ] **Step 6: Commit**

```bash
git add lumen/ui_v2/screens/*.py tests/ui/test_empty_states.py
git commit -m "Normalize empty/error states across screens

This commit used N prompts."
```

---

### Task 7: Keyboard-nav audit + document the keymap

**Files:**
- Modify: whichever of `lumen/ui_v2/main.py` / `screens/*.py` / `app.py` need a fix (only if the audit finds a gap)
- Modify: `.claude/skills/architecture.md` (add a short "Keyboard map" section)
- Test: `tests/ui/test_keyboard_nav.py` (only for any behavior you change; otherwise a documentation-only task)

**Interfaces:**
- Consumes: existing key handling in `main.py` (number-key tabs, Esc), launcher, chat, compose.
- Produces: a verified, documented keymap; fixes for any inconsistency found.

- [ ] **Step 1: Audit**

Grep and read the current handlers:

```bash
grep -rn "keyPressEvent\|Key_\|Qt.Key\|Escape\|Return\|Enter\|ControlModifier\|setShortcut" lumen/ui_v2
```

Verify: `1`–`6` switch tabs; `Esc` dismisses the launcher overlay and (where appropriate) returns focus / closes; `Ctrl+Return` submits in chat and compose; arrow keys move the launcher result selection; `Enter` in the todo input adds. Note any gap.

- [ ] **Step 2: Fix gaps (only if found) with a test**

If, e.g., `Ctrl+Return` doesn't submit in chat, write a failing test first:

```python
# tests/ui/test_keyboard_nav.py
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication
# build the relevant screen with a FakeClient (see test_chat_empty.py),
# post a Ctrl+Return QKeyEvent to the input, and assert a chat send happened.
```

Then implement the minimal handler and make it pass. If the audit finds no gaps, skip to Step 3.

- [ ] **Step 3: Document the keymap**

Add to `.claude/skills/architecture.md` a short section:

```markdown
## Keyboard map (Phase 10)
- `1`–`6` — switch tabs (Launcher/Dashboard/Calendar/Mail/Todos/Books/Chat)
- `Esc` — dismiss the launcher overlay; close compose/confirm
- `Ctrl+Return` — submit in Chat and Compose
- `↑`/`↓` — move selection in the launcher result list; `Return` runs it
- `Return` — add in the Todos input
```

Reconcile the list with what you actually verified in Step 1.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Audit and document keyboard navigation

This commit used N prompts."
```

---

## Live verification (after all tasks; verify skill)

1. Ensure a fresh daemon is running with the new code:
   `pgrep -af lumen-daemon` → kill any old one → `nohup uv run lumen-daemon > /tmp/phase10-daemon.log 2>&1 &`
2. Drive `settings.get` over the socket with a tiny asyncio client (`open_unix_connection`, `limit=16*1024*1024`); confirm `result.model.name` matches the running model and `accounts` reflects real token state.
3. Launch the UI (`uv run lumen-ui`), open Settings — values match the live daemon (no `llama3.1:8b` fixture), account status is real.
4. Open Chat with no conversation — the empty state shows; click an example prompt → a real conversation starts and streams.
5. Visit each screen with empty data — placeholders, not blank panes.

## Close-out

- Mark Phase 10 DONE in `.claude/skills/development-plan.md` with a dated Verified note.
- Record durable UI decisions (settings.get shape, reflect-only settings, empty_state helper, keymap) in `architecture.md`.

---

## Self-review notes

- **Spec coverage:** WS1 → Tasks 1,2,4; WS2 → Task 5; WS3 → Tasks 3,6; WS4 → Task 7. Accent persistence intentionally dropped (already exists via QSettings) — matches the corrected spec.
- **Type consistency:** `build_settings_snapshot(cfg) -> dict` used identically in Tasks 1/2; `empty_state(text, sub=None)` defined in Task 3 and consumed in Tasks 5/6; `fetch_settings(cb)` defined in Task 4 Step 1 and used in Step 4.
- **Known verification points for the implementer:** exact setter names on Todos/Books/Chat screens (Tasks 5,6 flag this — read the screen before writing the test); `ClickLabel` constructor signature (Task 5); whether `Qt` is already imported in `widgets.py` (Task 3).
