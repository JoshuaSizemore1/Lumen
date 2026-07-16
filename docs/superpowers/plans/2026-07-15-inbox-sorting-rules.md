# Inbox Sorting, Syncing & Rules — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the 2026-07-15 design of record in `new-features.md`: auto-mark-read on open (→ Gmail), label filter chips + colored tags with an INBOX-only default view, a deterministic no-LLM rules engine that labels-and-moves new mail on sync, three rule-creation paths (conversational via the local model, from an open email, Settings editor), and an on-request "Suggest labels" classifier.

**Architecture:** All Gmail label plumbing lives in `GmailSync`/`EmailStore` (`daemon/connectors/email_menu.py`); rules are a new `RuleStore` + pure matcher module (`daemon/connectors/mail_rules.py`) hooked into the incremental sync path. The router grows `rules.*` one-shots, an ungated `emails.auto_read`, a label-scoped `emails.list`, and two new local-model prompt assets (`daemon/llm/rule_author.py`, `daemon/llm/label_suggest.py`). The UI reads everything through `AppState` as today; the confirm overlay gains an optional checkbox so rule creation can offer "apply to N existing".

**Tech Stack:** Python 3.12, asyncio, SQLite (hand-written schema in `daemon/db.py`, `CREATE IF NOT EXISTS` = auto-migration), PyQt6, Ollama via `daemon/llm/client.py`, pytest (+ `QT_QPA_PLATFORM=offscreen` for UI tests).

## Global Constraints

- **Power/thermal (CLAUDE.md, non-negotiable):** the sync-path rules engine is deterministic — the poller must NEVER wake the LLM. The model runs only on explicit user request (conversational rule creation, Suggest button), then idle-unloads as usual.
- **UPSERT, never `INSERT OR REPLACE`, on `emails`** (FTS5 corruption — email-menu.md gotcha #1).
- **All LLM calls go through `daemon/llm/`** — never from `ui/` or `connectors/`.
- **Write-confirmation model (updated by this design):** archive keeps the confirm overlay. Read-state changes (auto + explicit) and rule-driven/suggestion-accepted label moves are pre-authorized: the dwell design decision, the rule-creation confirm, and the suggestion tap respectively ARE the confirmations. Rule creation always passes the daemon confirm gate.
- **Batched-LLM lesson (triage, live 2026-07-13):** the 4B model loses track of multi-message batches — "Suggest labels" classifies per-message in one button-press run (model loads once), not one mega-prompt. This satisfies the spec's intent (one press, one load/unload cycle).
- **Style:** terse over clever; comments only for constraints the code can't show; match surrounding idiom.
- **Commits:** every message ends with `This commit used 1 prompt.` (single user prompt drove this batch; repo is push-synced at 9e7e972). NO `Co-Authored-By` or credit trailers.
- **Test commands:** `python -m pytest tests/daemon -q` (daemon), `QT_QPA_PLATFORM=offscreen python -m pytest tests/ui -q` (UI). Run the whole suite before the final commit.
- **Working tree note:** `lumen/daemon/router.py` and `.claude/skills/llm-serving.md` carry uncommitted changes from the previous session (IDENTITY trim + brief mail-context). Task 0 commits them first so feature commits stay clean.

---

### Task 0: Commit carried-over work from the previous session

**Files:**
- Commit as-is: `lumen/daemon/router.py`, `.claude/skills/llm-serving.md`
- Commit new: `new-features.md`, `docs/superpowers/plans/2026-07-15-inbox-sorting-rules.md` (this plan)

- [ ] **Step 1: Verify the leftover diff is green**

Run: `python -m pytest tests/daemon -q`
Expected: all pass.

- [ ] **Step 2: Commit in two pieces**

```bash
git add lumen/daemon/router.py .claude/skills/llm-serving.md
git commit -m "Trim IDENTITY and add brief mail-context grounding for non-mail tool loops

Carried over from the previous session (warm-prime follow-on): the identity
block is tightened, and mail context riding along on a non-mail-shaped tool
loop now sends only counts + the search_email pointer instead of the full
unread list.

This commit used 1 prompt."
git add new-features.md docs/superpowers/plans/2026-07-15-inbox-sorting-rules.md
git commit -m "Add inbox sorting/rules design of record + implementation plan

This commit used 1 prompt."
```

---

### Task 1: Schema + EmailStore label map & scoped queries

**Files:**
- Modify: `lumen/daemon/db.py` (SCHEMA: `gmail_labels`, `mail_rules` tables)
- Modify: `lumen/daemon/connectors/email_menu.py` (EmailStore methods)
- Test: `tests/daemon/connectors/test_email_menu.py`

**Interfaces:**
- Produces: `EmailStore.set_labels(rows: list[dict])` (replace-all; rows `{"id","name","type"}`), `EmailStore.upsert_label(lid: str, name: str, type_: str = "user")`, `EmailStore.labels_map() -> dict[str,str]` (all labels, id→name), `EmailStore.user_labels() -> list[dict]` (`[{"id","name"}]`, type='user', name-sorted), `EmailStore.label_id(name: str) -> str | None` (user labels; exact then casefold), `EmailStore.present_label_ids() -> set[str]`, `EmailStore.list_page(filter, limit=50, offset=0, label_id=None)` with new filter `"label"`; filter `"unread"` becomes INBOX+UNREAD.

- [ ] **Step 1: Add failing tests** to `tests/daemon/connectors/test_email_menu.py`:

```python
def test_labels_map_and_user_labels(tmp_path):
    store = make_store(tmp_path)
    assert store.labels_map() == {} and store.user_labels() == []
    store.set_labels([{"id": "INBOX", "name": "INBOX", "type": "system"},
                      {"id": "Label_7", "name": "Bills", "type": "user"},
                      {"id": "Label_9", "name": "BSA", "type": "user"}])
    assert store.labels_map()["Label_7"] == "Bills"
    assert [l["name"] for l in store.user_labels()] == ["BSA", "Bills"]
    assert store.label_id("Bills") == "Label_7"
    assert store.label_id("bills") == "Label_7"          # casefold fallback
    assert store.label_id("INBOX") is None               # system labels hidden
    store.set_labels([{"id": "Label_9", "name": "BSA", "type": "user"}])
    assert store.label_id("Bills") is None               # replace-all
    store.upsert_label("Label_7", "Bills")
    assert store.label_id("Bills") == "Label_7"


def test_list_page_label_scope_and_present_ids(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1, labels=["INBOX", "UNREAD"]),
                  msg(2, labels=["Label_7"]),
                  msg(3, labels=["INBOX", "Label_7"])])
    assert [m["id"] for m in store.list_page("label", label_id="Label_7")] == ["m3", "m2"]
    assert store.present_label_ids() == {"INBOX", "UNREAD", "Label_7"}


def test_unread_filter_is_inbox_scoped(tmp_path):
    # Design 2026-07-15: labeled mail has left the inbox — "unread" means
    # INBOX + UNREAD, so rule-filed newsletters stop nagging chat/briefing.
    store = make_store(tmp_path)
    store.upsert([msg(1), msg(2, labels=["UNREAD"])])    # m2 unread but archived
    assert [m["id"] for m in store.list_page("unread")] == ["m1"]
    assert [m["id"] for m in store.unread()] == ["m1"]
```

Also update the two existing assertions this change breaks:
- `test_list_page_filters_and_order`: `list_page("unread")` now returns `["m1"]` (m2 has no INBOX).
- `test_update_labels_and_unread_and_counts`: after `add=["UNREAD"]` with INBOX removed, `unread()` returns `[]`; keep the `counts()` assertion unchanged (counts stay DB-wide `is_read` based).

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/daemon/connectors/test_email_menu.py -q`
Expected: FAIL (`set_labels` not defined, label filter KeyError).

- [ ] **Step 3: Implement.** In `db.py` SCHEMA (after the `emails_au` trigger):

```sql
CREATE TABLE IF NOT EXISTS gmail_labels (   -- name↔id map cached from labels.list
    id TEXT PRIMARY KEY,                    -- Gmail label id (Label_… for user labels)
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'user'       -- 'system' | 'user'
);
CREATE TABLE IF NOT EXISTS mail_rules (     -- deterministic inbox rules (no LLM at run time)
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,                    -- target label NAME (created in Gmail if missing)
    from_addrs TEXT NOT NULL DEFAULT '[]',  -- JSON arrays; a rule matches if ANY condition hits
    domains TEXT NOT NULL DEFAULT '[]',
    subject_kw TEXT NOT NULL DEFAULT '[]',
    body_kw TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
```

In `EmailStore` (after `update_labels`):

```python
    def set_labels(self, rows: list[dict]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM gmail_labels")
            self._conn.executemany(
                "INSERT INTO gmail_labels (id, name, type) VALUES (?, ?, ?)",
                [(r["id"], r["name"], r.get("type", "user")) for r in rows])

    def upsert_label(self, lid: str, name: str, type_: str = "user") -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO gmail_labels (id, name, type) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name",
                (lid, name, type_))

    def labels_map(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT id, name FROM gmail_labels").fetchall()
        return {r["id"]: r["name"] for r in rows}

    def user_labels(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, name FROM gmail_labels WHERE type = 'user' "
            "ORDER BY name COLLATE NOCASE").fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]

    def label_id(self, name: str) -> str | None:
        labels = self.user_labels()
        for l in labels:
            if l["name"] == name:
                return l["id"]
        want = name.casefold()
        return next((l["id"] for l in labels if l["name"].casefold() == want), None)

    def present_label_ids(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT labels FROM emails WHERE labels != ''").fetchall()
        return {l for r in rows for l in (r["labels"] or "").split(",") if l}
```

`list_page` becomes:

```python
    def list_page(self, filter: str = "inbox", limit: int = 50, offset: int = 0,
                  label_id: str | None = None) -> list[dict]:
        # "unread" is inbox-scoped: labeled mail has left the inbox (2026-07-15).
        where, params = {
            "inbox": ("WHERE (',' || labels || ',') LIKE '%,INBOX,%'", ()),
            "unread": ("WHERE is_read = 0 AND (',' || labels || ',') LIKE '%,INBOX,%'", ()),
            "label": ("WHERE (',' || labels || ',') LIKE ?", (f"%,{label_id},%",)),
            "all": ("", ()),
        }[filter]
        rows = self._conn.execute(
            f"SELECT * FROM emails {where} ORDER BY received_at DESC, id "
            f"LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        return [self._to_dict(r) for r in rows]
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/daemon/connectors/test_email_menu.py -q` → PASS; also `python -m pytest tests/daemon -q` (catch other users of `list_page("unread")`).

- [ ] **Step 5: Commit** — `git add lumen/daemon/db.py lumen/daemon/connectors/email_menu.py tests/daemon/connectors/test_email_menu.py && git commit` message: `Add gmail_labels/mail_rules schema + label-aware EmailStore queries` + trailer.

---

### Task 2: RuleStore + matchers (`mail_rules.py`)

**Files:**
- Create: `lumen/daemon/connectors/mail_rules.py`
- Test: `tests/daemon/connectors/test_mail_rules.py`

**Interfaces:**
- Produces: `RuleStore(conn)` with `list_all() -> list[dict]`, `enabled() -> list[dict]`, `get(rid) -> dict | None`, `add(rule: dict) -> dict`, `update(rid, rule) -> dict | None`, `set_enabled(rid, enabled)`, `delete(rid)`. Rule dict: `{"id", "label", "from_addrs", "domains", "subject_kw", "body_kw", "enabled", "created_at"}` (lists are real lists). Pure functions `sender_address(sender) -> str`, `rule_matches(rule, msg) -> bool`, `matching_labels(rules, msg) -> list[str]` (msg = mirror row with `sender/subject/body`).

- [ ] **Step 1: Write failing tests** `tests/daemon/connectors/test_mail_rules.py`:

```python
"""RuleStore CRUD + deterministic matchers."""
from lumen.daemon import db
from lumen.daemon.connectors.mail_rules import (
    RuleStore, matching_labels, rule_matches, sender_address)


def rule(**over):
    base = {"label": "BSA", "from_addrs": [], "domains": [],
            "subject_kw": [], "body_kw": []}
    base.update(over)
    return base


def row(**over):
    base = {"sender": "Troop 42 <news@Scouting.org>",
            "subject": "Pinewood Derby", "body": "See you at the pack meeting"}
    base.update(over)
    return base


def test_sender_address():
    assert sender_address("Ada <Ada@X.com>") == "ada@x.com"
    assert sender_address("bare@x.com") == "bare@x.com"
    assert sender_address("No Address Here") == ""


def test_matchers_any_condition_case_insensitive():
    assert rule_matches(rule(from_addrs=["NEWS@scouting.org"]), row())
    assert rule_matches(rule(domains=["scouting.org"]), row())
    assert rule_matches(rule(domains=["@scouting.org"]), row())      # tolerant
    assert rule_matches(rule(domains=["scouting.org"]),
                        row(sender="a <b@mail.scouting.org>"))       # subdomain
    assert not rule_matches(rule(domains=["couting.org"]), row())    # no suffix bleed
    assert rule_matches(rule(subject_kw=["pinewood"]), row())
    assert rule_matches(rule(body_kw=["PACK MEETING"]), row())
    assert not rule_matches(rule(subject_kw=["invoice"]), row())


def test_matching_labels_all_rules_dedup():
    rules = [rule(label="BSA", domains=["scouting.org"]),
             rule(label="Bills", subject_kw=["derby"]),
             rule(label="BSA", body_kw=["pack"])]
    assert matching_labels(rules, row()) == ["BSA", "Bills"]


def test_store_crud(tmp_path):
    store = RuleStore(db.connect(tmp_path / "r.db"))
    assert store.list_all() == []
    r = store.add(rule(subject_kw=["scout"]))
    assert r["id"] and r["enabled"] is True and r["subject_kw"] == ["scout"]
    assert store.get(r["id"])["label"] == "BSA"
    store.set_enabled(r["id"], False)
    assert store.enabled() == [] and store.list_all()[0]["enabled"] is False
    upd = store.update(r["id"], rule(label="Scouts", body_kw=["troop"]))
    assert upd["label"] == "Scouts" and upd["enabled"] is False   # update keeps enabled
    assert store.update(999, rule()) is None
    store.delete(r["id"])
    assert store.list_all() == []
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/daemon/connectors/test_mail_rules.py -q` → import error.

- [ ] **Step 3: Implement** `lumen/daemon/connectors/mail_rules.py`:

```python
"""Deterministic inbox rules: RuleStore (SQLite CRUD over mail_rules) + pure
matchers. Rules run in the sync path on newly-arrived mail — zero model cost
(design of record 2026-07-15); their writes were pre-authorized at creation."""

import json
import re
from datetime import datetime

_ADDR = re.compile(r"<([^<>\s]+@[^<>\s]+)>")
_LIST_COLS = ("from_addrs", "domains", "subject_kw", "body_kw")


def sender_address(sender: str) -> str:
    m = _ADDR.search(sender or "")
    if m:
        return m.group(1).casefold()
    s = (sender or "").strip()
    return s.casefold() if "@" in s else ""


def rule_matches(rule: dict, msg: dict) -> bool:
    """ANY condition hits: exact address, domain suffix, or case-insensitive
    substring on subject/body."""
    addr = sender_address(msg.get("sender", ""))
    if addr and addr in (a.casefold() for a in rule["from_addrs"]):
        return True
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    for d in rule["domains"]:
        d = d.casefold().lstrip("@")
        if d and (domain == d or domain.endswith("." + d)):
            return True
    subject = (msg.get("subject") or "").casefold()
    if any(k.casefold() in subject for k in rule["subject_kw"] if k):
        return True
    body = (msg.get("body") or "").casefold()
    return any(k.casefold() in body for k in rule["body_kw"] if k)


def matching_labels(rules: list[dict], msg: dict) -> list[str]:
    """Gmail-filter semantics: every matching rule applies; dedup, rule order."""
    out = []
    for r in rules:
        if r["label"] not in out and rule_matches(r, msg):
            out.append(r["label"])
    return out


class RuleStore:
    def __init__(self, conn):
        self._conn = conn

    @staticmethod
    def _to_dict(row) -> dict:
        d = dict(row)
        for c in _LIST_COLS:
            d[c] = json.loads(d[c] or "[]")
        d["enabled"] = bool(d["enabled"])
        return d

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM mail_rules ORDER BY created_at, id").fetchall()
        return [self._to_dict(r) for r in rows]

    def enabled(self) -> list[dict]:
        return [r for r in self.list_all() if r["enabled"]]

    def get(self, rid: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM mail_rules WHERE id = ?", (rid,)).fetchone()
        return self._to_dict(row) if row else None

    def add(self, rule: dict) -> dict:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO mail_rules (label, from_addrs, domains, subject_kw, "
                "body_kw, enabled, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                (rule["label"], *(json.dumps(rule[c]) for c in _LIST_COLS),
                 datetime.now().isoformat(timespec="seconds")))
        return self.get(cur.lastrowid)

    def update(self, rid: int, rule: dict) -> dict | None:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE mail_rules SET label = ?, from_addrs = ?, domains = ?, "
                "subject_kw = ?, body_kw = ? WHERE id = ?",
                (rule["label"], *(json.dumps(rule[c]) for c in _LIST_COLS), rid))
        return self.get(rid) if cur.rowcount else None

    def set_enabled(self, rid: int, enabled: bool) -> None:
        with self._conn:
            self._conn.execute("UPDATE mail_rules SET enabled = ? WHERE id = ?",
                               (int(enabled), rid))

    def delete(self, rid: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM mail_rules WHERE id = ?", (rid,))
```

- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** — `Add RuleStore + deterministic mail-rule matchers` + trailer.

---

### Task 3: GmailSync label plumbing + rules in the sync path

**Files:**
- Modify: `lumen/daemon/connectors/email_menu.py` (GmailSync)
- Test: `tests/daemon/connectors/test_email_menu.py` (extend; reuse the file's existing fake-service fixtures for sync tests — read them first)

**Interfaces:**
- Consumes: Task 1 store methods, Task 2 `RuleStore`/`matching_labels`.
- Produces: `GmailSync(store, google_cfg, sync_cfg, *, service_factory=None, rules: RuleStore | None = None)`; `await refresh_labels(service=None) -> bool`; `await create_label(name) -> str | None`; `await apply_label(mid, label_name) -> bool` (label = move: add id + remove INBOX, Gmail then mirror). Rules auto-apply inside `_incremental` only (never `_bulk` — first-run/re-baseline pulls are historical mail and must not mass-modify Gmail).

- [ ] **Step 1: Write failing tests.** Follow the existing fake-service pattern in `test_email_menu.py` (there are `GmailSync` tests below line 120 using `service_factory=`; mirror their fake construction). New tests:

```python
def test_refresh_labels_and_apply_label(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1)])
    calls = {}

    class FakeLabels:
        def list(self, userId):
            return FakeExec({"labels": [
                {"id": "INBOX", "name": "INBOX", "type": "system"},
                {"id": "Label_7", "name": "Bills", "type": "user"}]})
        def create(self, userId, body):
            calls["created"] = body["name"]
            return FakeExec({"id": "Label_9", "name": body["name"]})

    class FakeMessages:
        def modify(self, userId, id, body):
            calls["modify"] = (id, body)
            return FakeExec({})

    # FakeExec / FakeUsers / FakeService: shape after the file's existing fakes —
    # objects whose .execute() returns the canned dict.
    sync = GmailSync(store, None, None, service_factory=lambda: fake_service)

    asyncio.run(sync.refresh_labels())
    assert store.label_id("Bills") == "Label_7"

    assert asyncio.run(sync.apply_label("m1", "Bills")) is True
    assert calls["modify"] == ("m1", {"addLabelIds": ["Label_7"],
                                      "removeLabelIds": ["INBOX"]})
    got = store.get("m1")
    assert "Label_7" in got["labels"] and "INBOX" not in got["labels"]

    # unknown label -> created in Gmail first, then applied
    assert asyncio.run(sync.apply_label("m1", "BSA")) is True
    assert calls["created"] == "BSA" and store.label_id("BSA") == "Label_9"


def test_incremental_applies_rules_to_new_inbox_mail(tmp_path):
    # New INBOX message arriving via the History delta gets the rule's label
    # and loses INBOX — locally and via the Gmail modify call. SENT and
    # non-INBOX adds are untouched.
    ...  # build on the file's existing _incremental fake-history fixtures;
         # RuleStore seeded with rule label="Bills", domains=["duke-energy.com"]
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** In `GmailSync.__init__`, add `rules=None` param → `self._rules = rules`. Import at top of file: `from lumen.daemon.connectors import mail_rules`. In `_sync_once_locked`, after the `service is None` check:

```python
        await self.refresh_labels(service)   # name↔id map rides every sync
```

New methods (after `mark_read`):

```python
    async def refresh_labels(self, service=None) -> bool:
        """Cache the Gmail label name↔id map locally; failures never sink a
        sync (labels just go stale until the next poll)."""
        try:
            service = service or self._service_factory()
            if service is None:
                return False
            resp = await asyncio.to_thread(
                lambda: service.users().labels().list(userId="me").execute())
        except Exception:
            log.exception("gmail labels.list failed")
            return False
        self._store.set_labels([{"id": l["id"], "name": l.get("name", ""),
                                 "type": l.get("type", "user")}
                                for l in resp.get("labels", [])])
        return True

    async def create_label(self, name: str) -> str | None:
        try:
            service = (self._service_factory() if self._injected
                       else self._build_service(write=True))
        except Exception:
            log.exception("could not build gmail service")
            return None
        if service is None:
            return None
        try:
            created = await asyncio.to_thread(
                lambda: service.users().labels().create(
                    userId="me", body={"name": name}).execute())
        except Exception:
            # 409 = the name already exists upstream (stale local map):
            # re-pull the map and use the existing id.
            await self.refresh_labels()
            return self._store.label_id(name)
        self._store.upsert_label(created["id"], name)
        return created["id"]

    async def apply_label(self, mid: str, label_name: str) -> bool:
        """Label = move (design 2026-07-15): add the label, remove INBOX —
        Gmail first, then the mirror. Creates the Gmail label if missing."""
        label_id = self._store.label_id(label_name)
        if label_id is None:
            label_id = await self.create_label(label_name)
        if label_id is None:
            return False
        if not await self._modify(mid, {"addLabelIds": [label_id],
                                        "removeLabelIds": ["INBOX"]}):
            return False
        self._store.update_labels(mid, add=[label_id], remove=["INBOX"])
        return True

    async def _apply_rules(self, msgs: list[dict]) -> None:
        """Deterministic pass over newly-arrived mail — no LLM, ever, here.
        A failed apply just leaves the message in the inbox; never fails
        the sync."""
        rules = self._rules.enabled() if self._rules is not None else []
        if not rules:
            return
        for m in msgs:
            if "INBOX" not in m["labels"] or "SENT" in m["labels"]:
                continue
            for name in mail_rules.matching_labels(rules, m):
                try:
                    await self.apply_label(m["id"], name)
                except Exception:
                    log.exception("rule apply failed for %s", m["id"])
```

In `_incremental`, right after `self._store.upsert(msgs)`:

```python
                self._store.upsert(msgs)
                await self._apply_rules(msgs)   # new mail only — never the bulk pull
```

- [ ] **Step 4: Run** `python -m pytest tests/daemon -q` → PASS (existing sync tests must stay green; `refresh_labels` failures are non-fatal by design).
- [ ] **Step 5: Commit** — `Gmail label plumbing (fetch/create/apply-and-move) + rules engine in the incremental sync path` + trailer.

---

### Task 4: Confirm-with-checkbox protocol (daemon + UI)

**Files:**
- Modify: `lumen/daemon/router.py:531-538` (confirm.response)
- Modify: `lumen/ui_v2/confirm.py` (optional checkbox row)
- Modify: `lumen/ui_v2/state.py` (`respond_confirm` extra), `lumen/ui_v2/main.py:209-214` (`_on_confirm_result`)
- Modify: the UI client's `respond_confirm` (find it: `grep -rn "def respond_confirm" lumen/`)
- Test: `tests/daemon/test_router.py`, `tests/ui/test_ui_v2.py`

**Interfaces:**
- Produces: confirm_request payloads may carry `"check": {"label": str, "checked": bool}`. UI answers `confirm.response {confirm_id, approved, check?: bool}`. Broker waiters receive `False` (deny) / `True` (plain approve) / `{"approved": True, "check": bool}` (approve with checkbox) — dicts are truthy so existing bool-only waiters (`if not await wait(...)`) keep working unmodified.

- [ ] **Step 1: Failing daemon test** (in `test_router.py`, using its existing router fixture):

```python
async def test_confirm_response_carries_check(router_with_confirm):
    router, broker = router_with_confirm
    cid = broker.begin()
    async for _ in router.handle({"type": "confirm.response",
                                  "payload": {"confirm_id": cid,
                                              "approved": True, "check": True}}):
        pass
    assert await broker.wait(cid) == {"approved": True, "check": True}
    # plain approvals stay booleans
    cid = broker.begin()
    async for _ in router.handle({"type": "confirm.response",
                                  "payload": {"confirm_id": cid, "approved": True}}):
        pass
    assert await broker.wait(cid) is True
```

(Adapt entry-point/fixture names to the file's existing conventions — read its head first.)

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Router `confirm.response` branch becomes:

```python
        elif type_ == "confirm.response":
            # Silent ack: the answer unblocks whichever handler is awaiting it.
            # An approval may carry extras (rule-create checkbox) — those travel
            # as a truthy dict so bool-only waiters keep working.
            if self._confirm is not None:
                try:
                    answer: bool | dict = bool(payload["approved"])
                    if answer and "check" in payload:
                        answer = {"approved": True, "check": bool(payload["check"])}
                    self._confirm.resolve(int(payload["confirm_id"]), answer)
                except (KeyError, TypeError, ValueError):
                    log.warning("malformed confirm.response payload: %r", payload)
```

`ui_v2/confirm.py`: import `ClickLabel, TodoCheck` from `.widgets`; in `_build()` insert between the body widget and the foot:

```python
        self._check = None
        if c.get("check"):
            self._check = TodoCheck(bool(c["check"].get("checked", True)))
            crow = QWidget()
            cl = hbox(crow, (18, 0, 18, 12), 8)
            cl.addWidget(self._check)
            cl.addWidget(ClickLabel(c["check"]["label"], 11, T.TEXT_SECONDARY,
                                    on_click=self._check.click), 1)
            self.card_lay.addWidget(crow)
```

(also initialize `self._check = None` in `__init__`). `_finish` gains, before `cb(approved, payload)`:

```python
        if self._check is not None:
            payload = {**payload, "check_state": self._check.isChecked()}
```

`main.py::_on_confirm_result`:

```python
        if confirm_id is not None:          # daemon confirm-over-IPC: always answer
            self.state.respond_confirm(confirm_id, approved,
                                       check=payload.get("check_state"))
```

`state.py::respond_confirm` and the underlying client method both gain `check: bool | None = None`; the client includes `"check": check` in the confirm.response payload only when `check is not None`.

- [ ] **Step 4: UI test** (offscreen; follow `tests/ui/test_ui_v2.py` fixtures): open the overlay with a `check` payload, click confirm, assert `on_done` payload carries `check_state is True`; open without `check`, assert no `check_state` key.
- [ ] **Step 5: Run both suites** → PASS.
- [ ] **Step 6: Commit** — `Confirm overlay: optional checkbox riding the approval (rule-create backfill)` + trailer.

---

### Task 5: Router mail one-shots — label-aware list, auto-read, ungated mark-read, apply-label

**Files:**
- Modify: `lumen/daemon/router.py:701-750` (emails.* dispatch), `_gated_mail_action` (archive-only now)
- Test: `tests/daemon/test_router.py`

**Interfaces:**
- Consumes: Task 1/3 store + sync methods.
- Produces: `emails.list` accepts `{"filter": "inbox"|"unread"|"label", "label": name}` and returns per-row `label_names: [str]` plus top-level `labels: [str]` (user-label names present in the mirror, casefold-sorted). `emails.search` rows also carry `label_names`. New ops: `emails.auto_read {id}` (silent, ungated, no-op when already read), `emails.apply_label {id, label}` (ungated — the tap is the accept). `emails.mark_read` no longer confirms (design 2026-07-15: read-state is pre-authorized); `emails.archive` still does. `mail.refresh` passes filter keys through to the reloaded list. Router helpers `_with_label_names(rows) -> rows`, `_present_user_labels() -> list[str]`.

- [ ] **Step 1: Failing tests** (adapt to `test_router.py` fixtures/fakes — read its mail fakes first):

```python
async def test_emails_list_carries_label_names_and_scopes(...):
    # seed: gmail_labels {Label_7: Bills}; m1 INBOX+Label_7, m2 Label_7 only
    # emails.list {} -> rows [m1], m1.label_names == ["Bills"], labels == ["Bills"]
    # emails.list {"filter": "label", "label": "Bills"} -> [m1, m2]
    # emails.list {"filter": "label", "label": "Nope"} -> error "unknown label"

async def test_auto_read_is_silent_and_ungated(...):
    # unread m1: emails.auto_read {"id": "m1"} -> single {"result": {"ok": True}},
    # fake mark_read called with (m1, True); repeat on read mail -> no gmail call

async def test_mark_read_no_longer_confirms(...):
    # emails.mark_read yields result directly — no confirm_request event

async def test_apply_label_op(...):
    # emails.apply_label {"id": "m1", "label": "Bills"} -> ok True,
    # fake apply_label called; missing id/label -> error
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Router helpers (near `_gated_mail_action`):

```python
    def _with_label_names(self, rows: list[dict]) -> list[dict]:
        m = {l["id"]: l["name"] for l in self._mail_store.user_labels()}
        for r in rows:
            r["label_names"] = [m[i] for i in r.get("labels", []) if i in m]
        return rows

    def _present_user_labels(self) -> list[str]:
        m = {l["id"]: l["name"] for l in self._mail_store.user_labels()}
        present = self._mail_store.present_label_ids()
        return sorted((m[i] for i in present if i in m), key=str.casefold)
```

Dispatch changes inside the `emails.` branch:

```python
            if type_ == "mail.refresh":
                if not (self._mail.busy or self._mail.syncing):
                    await self._mail.sync_once()
                # keep the caller's scope so a label view reloads as itself
                type_, payload = "emails.list", {
                    k: payload[k] for k in ("filter", "label", "limit", "offset")
                    if k in payload}
            if type_ == "emails.list":
                filt = payload.get("filter", "inbox")
                label_id = None
                if filt == "label":
                    label_id = self._mail_store.label_id(str(payload.get("label", "")))
                    if label_id is None:
                        yield {"error": "unknown label"}
                        return
                yield {"result": {
                    "emails": self._with_label_names(self._mail_store.list_page(
                        filt, int(payload.get("limit", 50)),
                        int(payload.get("offset", 0)), label_id=label_id)),
                    "labels": self._present_user_labels(),
                    "connected": self._mail.connected,
                    "syncing": self._mail.syncing,
                    "last_sync": self._mail.last_sync(),
                    "counts": self._mail_store.counts()}}
            elif type_ == "emails.search":
                yield {"result": {"emails": self._with_label_names(
                    self._mail_store.search(payload.get("query", ""),
                                            int(payload.get("limit", 50))))}}
```

Replace the `elif type_ in ("emails.archive", "emails.mark_read")` pair with:

```python
            elif type_ == "emails.archive":
                async for ev in self._gated_mail_action(type_, payload):
                    yield ev
            elif type_ == "emails.mark_read":
                # Read-state writes stopped confirming 2026-07-15 — opening a
                # message auto-marks it read, so the explicit button can't
                # rank a dialog above the same silent write.
                row = self._mail_store.get(str(payload.get("id", "")))
                if row is None:
                    yield {"error": "email not found"}
                    return
                ok = await self._mail.mark_read(row["id"],
                                                bool(payload.get("read", True)))
                yield {"result": {"ok": ok, "message": "Updated." if ok else
                                  "Couldn't reach Gmail — nothing was changed."}}
            elif type_ == "emails.auto_read":
                # Dwell-timer read receipt: silent and idempotent.
                row = self._mail_store.get(str(payload.get("id", "")))
                if row is not None and not row["is_read"]:
                    await self._mail.mark_read(row["id"], True)
                yield {"result": {"ok": True}}
            elif type_ == "emails.apply_label":
                mid = str(payload.get("id", ""))
                name = str(payload.get("label", "")).strip()
                if not name or self._mail_store.get(mid) is None:
                    yield {"error": "emails.apply_label needs {id, label}"}
                    return
                ok = await self._mail.apply_label(mid, name)
                yield {"result": {"ok": ok, "message":
                       f"Labeled {name} — moved out of inbox." if ok else
                       "Couldn't reach Gmail — nothing was changed."}}
```

Simplify `_gated_mail_action` to the archive-only shape (drop the `read` branching; title `"Archive email"`, verb `"Archive"`). Delete the now-dead mark-read wording. Update any existing router tests that asserted the mark-read confirm.

- [ ] **Step 4: Run** `python -m pytest tests/daemon -q` → PASS.
- [ ] **Step 5: Commit** — `Label-aware emails.list/search, silent auto-read, ungated mark-read, apply_label op` + trailer.

---

### Task 6: Router rules ops + gated create with backfill; daemon wiring

**Files:**
- Modify: `lumen/daemon/router.py` (rules.* dispatch, `_gated_rule_save`, `rules=` param), `lumen/daemon/__main__.py` (wire `RuleStore`)
- Test: `tests/daemon/test_router.py`

**Interfaces:**
- Consumes: Task 2 `RuleStore`, Task 3 `apply_label`, Task 4 checkbox answers, Task 7's `validate_rule` — **define `validate_rule` in `lumen/daemon/llm/rule_author.py` in THIS task** (the prompt half arrives in Task 7).
- Produces: ops `rules.list {} -> {"rules", "labels"}` (labels = all user-label names, for pickers), `rules.create {"rule"} -> confirm_request…, {"ok","message","rules"}`, `rules.update {"id","rule"}`, `rules.delete {"id"}`, `rules.toggle {"id","enabled"}` (all three return `{"ok": True, "rules": [...]}`; update/delete/toggle are local-only writes — ungated, like todos). Router constructor param `rules=None`. Constant `RULE_SCAN_LIMIT = 500`.

- [ ] **Step 1: Create `lumen/daemon/llm/rule_author.py`** with just the mechanical gate (test in Task 7 covers it too, but write these tests now in `tests/daemon/llm/test_rule_author.py`):

```python
"""Rule-authoring for the LOCAL model (a prompt asset, not a .claude skill):
turns a fuzzy 'filter X as Y' request into a concrete mail rule. Runs only on
an explicit create-a-rule request, then unloads — power budget. Mechanical
validation gate, book_recs rule-class."""

MAX_KW = 10
MAX_LABEL = 60


def validate_rule(p: dict) -> dict | None:
    """Mechanical gate: a usable rule needs a label and ≥1 condition. Lists
    are cleaned (strings only, stripped, deduped, capped) — never invented."""
    if not isinstance(p, dict):
        return None
    label = str(p.get("label") or "").strip().strip("/")
    if not label or len(label) > MAX_LABEL:
        return None

    def clean(key):
        out = []
        for v in p.get(key) or []:
            v = str(v).strip()
            if v and v.casefold() not in (o.casefold() for o in out):
                out.append(v)
        return out[:MAX_KW]

    rule = {"label": label,
            **{c: clean(c) for c in ("from_addrs", "domains",
                                     "subject_kw", "body_kw")}}
    if not any(rule[c] for c in ("from_addrs", "domains",
                                 "subject_kw", "body_kw")):
        return None
    return rule
```

Tests: valid rule round-trips; missing label → None; no conditions → None; non-string junk dropped; dedup casefold; caps at 10; extra keys (`create_label_if_missing`) ignored.

- [ ] **Step 2: Failing router tests** — `rules.list` empty; `rules.create` yields a confirm_request whose rows include the label and an `Existing matches` row, `check` present only when matches > 0; approving with `{"approved": True, "check": True}` saves the rule AND calls the fake `apply_label` for each matching inbox mail; deny saves nothing; `rules.toggle`/`rules.delete`/`rules.update` round-trip.

- [ ] **Step 3: Implement.** Router `__init__`: add `rules=None` → `self._rules = rules`. Import: `from lumen.daemon.connectors import mail_rules` and `from lumen.daemon.llm.rule_author import validate_rule` (Task 7 extends this import with `propose_rule`). Constant near other caps: `RULE_SCAN_LIMIT = 500`.

Dispatch branch (before the final `else`):

```python
        elif type_.startswith("rules."):
            if (self._rules is None or self._mail is None
                    or self._mail_store is None or self._confirm is None):
                yield {"error": "mail rules unavailable"}
                return
            if type_ == "rules.list":
                yield {"result": {"rules": self._rules.list_all(),
                                  "labels": [l["name"] for l in
                                             self._mail_store.user_labels()]}}
            elif type_ == "rules.create":
                rule = validate_rule(payload.get("rule") or {})
                if rule is None:
                    yield {"error": "a rule needs a label and at least one condition"}
                    return
                out = {}
                async for ev in self._gated_rule_save(rule):
                    if "_saved" in ev:
                        out = ev
                    else:
                        yield ev
                msg = (f"Rule saved — {out.get('_applied', 0)} existing "
                       "email(s) labeled." if out.get("_saved")
                       else "Cancelled — nothing was saved.")
                yield {"result": {"ok": bool(out.get("_saved")), "message": msg,
                                  "rules": self._rules.list_all()}}
            elif type_ == "rules.update":
                rule = validate_rule(payload.get("rule") or {})
                try:
                    rid = int(payload["id"])
                except (KeyError, TypeError, ValueError):
                    rid, rule = 0, None
                if rule is None or self._rules.update(rid, rule) is None:
                    yield {"error": "rules.update needs {id, rule}"}
                    return
                yield {"result": {"ok": True, "rules": self._rules.list_all()}}
            elif type_ == "rules.toggle":
                try:
                    self._rules.set_enabled(int(payload["id"]),
                                            bool(payload["enabled"]))
                except (KeyError, TypeError, ValueError):
                    yield {"error": "rules.toggle needs {id, enabled}"}
                    return
                yield {"result": {"ok": True, "rules": self._rules.list_all()}}
            elif type_ == "rules.delete":
                try:
                    self._rules.delete(int(payload["id"]))
                except (KeyError, TypeError, ValueError):
                    yield {"error": "rules.delete needs {id}"}
                    return
                yield {"result": {"ok": True, "rules": self._rules.list_all()}}
            else:
                yield {"error": f"unknown request type: {type_}"}
```

`_gated_rule_save` (near `_gated_mail_action`):

```python
    async def _gated_rule_save(self, rule: dict):
        """Confirm-over-IPC for a new rule: the summary rows are the plain-
        language rendering, the checkbox offers the backfill, and the user's
        Save is the pre-authorization for every future auto-apply. Yields UI
        events, then a final {"_saved", "_applied", "_matched"}."""
        inbox = self._mail_store.list_page("inbox", limit=RULE_SCAN_LIMIT)
        matches = [m["id"] for m in inbox if mail_rules.rule_matches(rule, m)]
        rows = [("Label", rule["label"])]
        for key, name in (("from_addrs", "From"), ("domains", "Domains"),
                          ("subject_kw", "Subject"), ("body_kw", "Body")):
            if rule[key]:
                rows.append((name, ", ".join(rule[key])))
        rows.append(("Existing", f"{len(matches)} matching in your inbox"))
        confirm_id = self._confirm.begin()
        req = {"icon": "⚑", "title": "Create mail rule",
               "intro": "New mail matching this rule is labeled and moved out "
                        "of your inbox automatically — in Gmail too. Saving "
                        "pre-approves those moves.",
               "rows": rows, "confirm_label": "Save rule"}
        if matches:
            req["check"] = {"label": f"Also label the {len(matches)} matching "
                                     "email(s) already in your inbox",
                            "checked": True}
        yield {"confirm_request": req, "confirm_id": confirm_id}
        answer = await self._confirm.wait(confirm_id)
        if not answer:
            yield {"_saved": False, "_applied": 0, "_matched": len(matches)}
            return
        self._rules.add(rule)
        self._log("email", "query", {"action": "create_rule",
                                     "label": rule["label"]})
        applied = 0
        if isinstance(answer, dict) and answer.get("check"):
            for mid in matches:
                if await self._mail.apply_label(mid, rule["label"]):
                    applied += 1
        yield {"_saved": True, "_applied": applied, "_matched": len(matches)}
```

`__main__.py`: `from lumen.daemon.connectors.mail_rules import RuleStore` (match the file's import style); then:

```python
    rules = RuleStore(conn)
    mail = GmailSync(emails, cfg.google, cfg.sync, rules=rules)
    ...
    router = Router(..., mail=mail, mail_store=emails, rules=rules, ...)
```

- [ ] **Step 4: Run** daemon suite → PASS.
- [ ] **Step 5: Commit** — `rules.* ops with confirm-gated create + inbox backfill; wire RuleStore into daemon` + trailer.

---

### Task 7: Conversational rule creation (prompt asset + router intent)

**Files:**
- Modify: `lumen/daemon/llm/rule_author.py` (add SYSTEM + `propose_rule`)
- Modify: `lumen/daemon/router.py` (RULE_HINT, `_create_rule_chat`, chat chain)
- Test: `tests/daemon/llm/test_rule_author.py`, `tests/daemon/test_router.py`

**Interfaces:**
- Consumes: `validate_rule` (Task 6), `parse_proposal` from `lumen.daemon.llm.event_create`, `_gated_rule_save` (Task 6).
- Produces: `await propose_rule(llm, message, labels: list[str]) -> tuple[dict | None, str | None]` — one fast-model generation, no tools.

- [ ] **Step 1: Failing tests.** `test_rule_author.py` (mirror the FakeLLM streaming pattern in `tests/daemon/llm/test_email_compose.py` — read it first): a canned JSON reply parses + validates; prose-wrapped JSON still parses (`parse_proposal` handles it); garbage → `(None, err)` with a helpful message; existing labels ride in the user turn. `test_router.py`: a message `"create a rule to filter all emails relating to boy scouts as BSA"` routes to the rule path (yields a confirm_request, not a compose_request — regression: COMPOSE_HINT's `e-?mail…to` alternation would otherwise steal "emails relating to").

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Append to `rule_author.py`:

```python
from lumen.daemon.llm.event_create import parse_proposal

SYSTEM = (
    "You turn the user's request into a Gmail filing rule. Reply with ONLY a "
    "JSON object, no prose, shaped exactly:\n"
    '{"label": "...", "from_addrs": [], "domains": [], "subject_kw": [], '
    '"body_kw": []}\n'
    "The rule files matching mail under the label. Expand the user's concept "
    "into generous lowercase keyword lists for subject_kw and body_kw — "
    "synonyms, related words, organization names. Add sender domains only "
    "when you are confident; from_addrs only for addresses the user wrote. "
    "Prefer one of the user's existing labels when one fits; otherwise a "
    "short new label name.\n"
    'Example — "filter all emails relating to boy scouts as BSA":\n'
    '{"label": "BSA", "from_addrs": [], "domains": ["scouting.org"], '
    '"subject_kw": ["boy scout", "cub scout", "scouting", "troop", "BSA"], '
    '"body_kw": ["boy scout", "cub scout", "scouting", "troop", "BSA"]}'
)


async def propose_rule(llm, message: str, labels: list[str]
                       ) -> tuple[dict | None, str | None]:
    """One structured generation on the fast model — no tools, no chain."""
    user = (f"Existing labels: {', '.join(labels)}\n\nRequest: {message}"
            if labels else message)
    text = ""
    async for chunk in llm.chat([{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": user}]):
        text += chunk
    rule = validate_rule(parse_proposal(text) or {})
    if rule is None:
        return None, ("I couldn't turn that into a rule — tell me what to "
                      "match (a sender, a domain, or keywords) and what "
                      "label to file it under.")
    return rule, None
```

Router: import `propose_rule` alongside `validate_rule`. Hint (near COMPOSE_HINT, with this comment):

```python
# Rule creation must outrank COMPOSE ("filter all emails relating to X" hits
# COMPOSE's "email…to" alternation) — checked right before it.
RULE_HINT = re.compile(
    r"\b(?:create|add|make|set\s*up|new)\b.{0,40}\b(?:rule|filter)\b"
    r"|\brule\b.{0,40}\b(?:label|filter|move|file)\b"
    r"|\balways\s+(?:label|file|move|filter)\b",
    re.IGNORECASE | re.DOTALL)
```

In `_chat`'s elif chain, insert immediately BEFORE the COMPOSE_HINT branch:

```python
        elif (self._rules is not None and self._mail is not None
                and self._confirm is not None and RULE_HINT.search(message)):
            sub, subsystem = self._create_rule_chat(message), "email"
```

Handler (near `_compose_email_chat`):

```python
    async def _create_rule_chat(self, message: str):
        """NL → structured rule (local model) → confirm overlay → save/backfill.
        The overlay's rows are the plain-language rendering of the parsed rule."""
        labels = [l["name"] for l in self._mail_store.user_labels()]
        try:
            rule, err = await propose_rule(self._llm, message, labels)
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        if rule is None:
            yield {"chunk": err}
            yield {"done": True}
            return
        out = {}
        async for ev in self._gated_rule_save(rule):
            if "_saved" in ev:
                out = ev
            else:
                yield ev
        if out.get("_saved"):
            extra = (f" I also labeled {out['_applied']} matching email(s) "
                     "already in your inbox." if out.get("_applied") else "")
            yield {"chunk": f"Done — new mail matching this gets “{rule['label']}” "
                            f"and leaves your inbox.{extra}"}
        else:
            yield {"chunk": "Cancelled — no rule was saved."}
        yield {"done": True}
```

- [ ] **Step 4: Run** daemon suite → PASS.
- [ ] **Step 5: Commit** — `Conversational rule creation: local-model prompt asset + RULE_HINT chat route` + trailer.

---

### Task 8: "Suggest labels" — classifier + one-shot op

**Files:**
- Create: `lumen/daemon/llm/label_suggest.py`
- Modify: `lumen/daemon/router.py` (op `mail.suggest_labels`)
- Test: `tests/daemon/llm/test_label_suggest.py`, `tests/daemon/test_router.py`

**Interfaces:**
- Consumes: `message_line` from `lumen.daemon.llm.triage`.
- Produces: `parse_choice(text, labels) -> str | None` (casefold match against the user's label names; null/unknown → None), `await suggest(llm, row, labels) -> str | None`. Op `mail.suggest_labels {} -> {"suggestions": {mid: label_name}, "scanned": int}`. Constants `SUGGEST_SCAN_LIMIT = 200`, `SUGGEST_LIMIT = 15` (15 sequential warm-model calls ≈ tens of seconds worst case on the serial data channel — the UI disables the button while waiting; a bigger cap would stall other one-shots).

- [ ] **Step 1: Failing tests.** `test_label_suggest.py`: exact name match; casefold match returns the canonical name; `{"label": null}` → None; a label NOT in the list → None (never invent); prose-wrapped JSON ok; garbage → None. Router test: fake LLM returning `{"label": "Bills"}` → suggestions map only for INBOX rows carrying no user label; already-labeled and non-inbox rows skipped; no user labels in store → error.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** `label_suggest.py`:

```python
"""'Suggest labels': one tiny per-message verdict against the user's existing
label names (the 4B loses track of batches — triage lesson 2026-07-13). Runs
ONLY on the explicit button press, then unloads; nothing is written until the
user taps a suggestion (the tap is the accept)."""

import json
import re

from lumen.daemon.llm.triage import message_line

_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM_TMPL = (
    "You file ONE email under one of the user's Gmail labels: {labels}.\n"
    "Pick the single best-fitting label, or null if none clearly fits — "
    "never invent a new label. Reply with ONLY a JSON object: "
    '{{"label": "..."}} or {{"label": null}}.'
)


def parse_choice(text: str, labels: list[str]) -> str | None:
    """Only a name from `labels` survives — an unknown or null pick leaves
    the message honestly unsuggested, it never guesses."""
    m = _OBJECT.search(text or "")
    if m is None:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    name = obj.get("label") if isinstance(obj, dict) else None
    if not isinstance(name, str):
        return None
    return {l.casefold(): l for l in labels}.get(name.strip().casefold())


async def suggest(llm, row: dict, labels: list[str]) -> str | None:
    """One verdict for one message. LLMUnavailable propagates."""
    text = ""
    async for chunk in llm.chat(
            [{"role": "system",
              "content": SYSTEM_TMPL.format(labels=", ".join(labels))},
             {"role": "user", "content": message_line(row)}]):
        text += chunk
    return parse_choice(text, labels)
```

Router: `from lumen.daemon.llm import label_suggest`; constants near `RULE_SCAN_LIMIT`; op inside the `emails./mail.` guard block:

```python
            elif type_ == "mail.suggest_labels":
                # Explicit press only: per-message verdicts (triage lesson —
                # the 4B loses a 20-message batch), one load/unload cycle.
                user = self._mail_store.user_labels()
                if not user:
                    yield {"error": "no Gmail labels yet — create a rule or a "
                                    "label first"}
                    return
                names = [l["name"] for l in user]
                ids = {l["id"] for l in user}
                rows = [r for r in self._mail_store.list_page(
                            "inbox", limit=SUGGEST_SCAN_LIMIT)
                        if not ids.intersection(r["labels"])][:SUGGEST_LIMIT]
                suggestions = {}
                try:
                    for r in rows:
                        name = await label_suggest.suggest(self._llm, r, names)
                        if name:
                            suggestions[r["id"]] = name
                except LLMUnavailable as e:
                    yield {"error": str(e)}
                    return
                yield {"result": {"suggestions": suggestions,
                                  "scanned": len(rows)}}
```

(Register the op: extend the branch condition at line ~701 to `type_.startswith("emails.") or type_ in ("mail.refresh", "mail.suggest_labels")`.)

- [ ] **Step 4: Run** daemon suite → PASS.
- [ ] **Step 5: Commit** — `Suggest-labels classifier: per-message verdicts on explicit request only` + trailer.

---

### Task 9: UI plumbing — theme palette, FlowLayout/ClickChip, AppState, sample data

**Files:**
- Modify: `lumen/ui_v2/theme.py` (LABEL_PALETTE + `label_color`), `lumen/ui_v2/widgets.py` (`ClickChip`, `FlowLayout`), `lumen/ui_v2/state.py`, `lumen/ui_v2/sample_data.py`
- Test: `tests/ui/test_ui_v2.py` (or the file the suite keeps state tests in — follow local convention)

**Interfaces:**
- Produces: `T.label_color(name) -> str` (stable across runs — crc32, NOT `hash()` which is salted per process). `ClickChip(text, fg, border, bg=None, px=10, on_click=None, tooltip="")`. `FlowLayout(parent=None, hgap=6, vgap=6)`. AppState: `mail_scope` ("all" | "unread" | label name), `mail_labels: list[str]`, `mail_suggestions: dict[str, str]`, `set_mail_scope(scope)`, `auto_read(mid)`, `suggest_labels(cb=None)`, `apply_suggestion(mid)`, `list_rules(cb)`, `create_rule(rule, cb=None)`, `update_rule(rid, rule, cb=None)`, `delete_rule(rid, cb=None)`, `toggle_rule(rid, enabled, cb=None)`, `open_rule_editor(prefill=None)` + signal `rule_edit_requested = pyqtSignal(dict)`. `_norm_mail` rows gain `"label_names"`.

- [ ] **Step 1: Failing tests:** `label_color` returns a palette member and is stable for the same name across calls; `_norm_mail` carries `label_names`; sample-mode `AppState.set_mail_scope("Bills")` flips `mail_scope` + emits `mails_changed`; sample-mode `auto_read` flips the row's `unread` and emits; `apply_suggestion` pops the suggestion.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.**

`theme.py` (after TAG_COLORS; add `import zlib` at top):

```python
# Mail label pills: stable color per label name (crc32 — Python's hash() is
# salted per process and would reshuffle colors every launch).
LABEL_PALETTE = ("#7aa2f7", "#bb9af7", "#9ece6a", "#e0af68",
                 "#7dcfff", "#f7768e", "#ff9e64", "#73daca")


def label_color(name: str) -> str:
    return LABEL_PALETTE[zlib.crc32(name.casefold().encode()) % len(LABEL_PALETTE)]
```

`widgets.py` — add `QLayout`, `QPoint`, `QRect` to the Qt imports, then:

```python
class ClickChip(Chip):
    """Chip that emits a callback on click (mail filter chips, suggestions)."""

    def __init__(self, text: str, fg: str, border: str, bg: str | None = None,
                 px: int = 10, on_click=None, tooltip: str = ""):
        super().__init__(text, fg, border, bg, px=px, radius=8, hpad=8, vpad=3)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        self._on_click = on_click

    def mousePressEvent(self, ev):
        if self._on_click and ev.button() == Qt.MouseButton.LeftButton:
            self._on_click()


class FlowLayout(QLayout):
    """Left-aligned wrapping layout (label chip rows in a fixed-width column)."""

    def __init__(self, parent=None, hgap: int = 6, vgap: int = 6):
        super().__init__(parent)
        self._items, self._h, self._v = [], hgap, vgap
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._arrange(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _arrange(self, rect, test_only: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            if x + w > rect.right() + 1 and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._v, 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x += w + self._h
            line_h = max(line_h, h)
        return y + line_h - rect.y()
```

`state.py` — `_norm_mail` gains `"label_names": r.get("label_names", [])`. In `__init__` (both modes, before the live/sample split): `self.mail_scope = "all"`, `self.mail_suggestions = {}`; live sets `self.mail_labels = []`, sample sets `self.mail_labels = sorted({n for m in self.mails for n in m.get("label_names", [])})`. New/changed methods in the mail section:

```python
    def set_mail_scope(self, scope: str) -> None:
        if scope == self.mail_scope:
            return
        self.mail_scope = scope
        self.mail_suggestions.clear()
        if self.live:
            self.refresh_mails()
        else:
            self.mails_changed.emit()   # sample mode: chips reflect selection only

    def _scope_payload(self) -> dict:
        if self.mail_scope == "unread":
            return {"filter": "unread"}
        if self.mail_scope == "all":
            return {"filter": "inbox"}
        return {"filter": "label", "label": self.mail_scope}

    def refresh_mails(self) -> None:
        if self._data is not None:
            self._data.request("emails.list", self._scope_payload(), self._set_mails)

    def refresh_inbox(self) -> None:
        """Manual refresh: delta-sync against Gmail, then reload the current scope."""
        if self._data is not None:
            self._data.request("mail.refresh", self._scope_payload(), self._set_mails)
```

`_set_mails` additions (after the existing status fallbacks):

```python
        self.mail_labels = result.get("labels", self.mail_labels)
        if (self.mail_scope not in ("all", "unread")
                and self.mail_scope not in self.mail_labels):
            self.mail_scope = "all"   # scope label vanished upstream
```

```python
    def auto_read(self, mid: str) -> None:
        """Dwell-timer read receipt: silent, ungated, flips the row locally
        so the dot clears immediately (design 2026-07-15)."""
        m = next((x for x in self.mails if x["id"] == mid), None)
        if m is None or not m["unread"]:
            return
        m["unread"] = False
        self.mails_changed.emit()
        if self._data is not None:
            self._data.request("emails.auto_read", {"id": mid}, lambda _r: None)

    def suggest_labels(self, cb=None) -> None:
        """One explicit press → classify unlabeled inbox mail; results stay
        chips until tapped — nothing is written until accept."""
        if self._data is None:
            if cb:
                cb({})
            return

        def handle(result):
            self.mail_suggestions = dict(
                (result or {}).get("suggestions", {}))
            self.mails_changed.emit()
            if cb:
                cb(result)
        self._data.request("mail.suggest_labels", {}, handle)

    def apply_suggestion(self, mid: str) -> None:
        name = self.mail_suggestions.pop(mid, None)
        if name is None:
            return
        if self._data is not None:
            self._data.request("emails.apply_label", {"id": mid, "label": name},
                               self._mail_action_done)
        else:
            self.mails_changed.emit()

    # ---- mail rules (Phase 12) ----
    def open_rule_editor(self, prefill: dict | None = None) -> None:
        self.rule_edit_requested.emit(prefill or {})

    def list_rules(self, cb) -> None:
        if self._data is not None:
            self._data.request("rules.list", {}, cb)
        else:
            cb({"rules": [], "labels": []})

    def create_rule(self, rule: dict, cb=None) -> None:
        """Daemon gates creation behind the confirm overlay (summary + the
        apply-to-existing checkbox); Save pre-authorizes future auto-applies."""
        if self._data is not None:
            self._data.request("rules.create", {"rule": rule},
                               cb or (lambda _r: None))

    def update_rule(self, rid: int, rule: dict, cb=None) -> None:
        if self._data is not None:
            self._data.request("rules.update", {"id": rid, "rule": rule},
                               cb or (lambda _r: None))

    def delete_rule(self, rid: int, cb=None) -> None:
        if self._data is not None:
            self._data.request("rules.delete", {"id": rid},
                               cb or (lambda _r: None))

    def toggle_rule(self, rid: int, enabled: bool, cb=None) -> None:
        if self._data is not None:
            self._data.request("rules.toggle", {"id": rid, "enabled": enabled},
                               cb or (lambda _r: None))
```

Signal (with the others): `rule_edit_requested = pyqtSignal(dict)  # open the rule editor (prefill payload)`.

`sample_data.py`: give two MAILS entries `"label_names"` (e.g. add `"label_names": ["Bills"]` to one and `"label_names": ["BSA"]` to another) so chips/pills show in screenshots and sample-mode tests.

- [ ] **Step 4: Run** `QT_QPA_PLATFORM=offscreen python -m pytest tests/ui -q` → PASS.
- [ ] **Step 5: Commit** — `UI plumbing for labels/rules: stable label colors, flow layout, AppState scope/suggest/rule methods` + trailer.

---

### Task 10: Mail screen — chips, pills, dwell timer, suggest + rule buttons

**Files:**
- Modify: `lumen/ui_v2/screens/mail.py`
- Test: `tests/ui/test_ui_v2.py` (or `tests/ui/test_screens.py` — follow where MailScreen is already covered)

**Interfaces:**
- Consumes: everything from Task 9.
- Produces: user-visible batch 1 + 3 behavior. Chip row (All · Unread · one per `state.mail_labels`), colored pills on rows + reading pane, 1s dwell auto-read, ✨ suggest button, ⚑ rule-from-email button.

- [ ] **Step 1: Failing tests** (sample mode, offscreen): building MailScreen shows chips `["All", "Unread", "BSA", "Bills"]` (from sample labels); clicking a label chip calls `set_mail_scope`; a row for a mail with `label_names` contains a Chip with that text; selecting an unread mail then firing `screen._dwell.timeout` marks it read in state; the suggest button exists and disables on click.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** in `mail.py`. Imports: add `Chip, ClickChip, FlowLayout` to the widgets import. In `__init__` after `self.status_lab`:

```python
        chips_host = QWidget()
        self.chips_lay = FlowLayout(chips_host)
        hv.addWidget(chips_host)
```

Suggest button in `top_row` (after the refresh button):

```python
        self.suggest_btn = button("✨", "outline", px=13)
        self.suggest_btn.setFixedSize(28, 26)
        self.suggest_btn.setToolTip(
            "Suggest labels for unlabeled mail (runs the local model once)")
        self.suggest_btn.clicked.connect(self._suggest)
        top_row.addWidget(self.suggest_btn)
```

Dwell timer in `__init__` (near the search debounce timer):

```python
        # dwell auto-read: a message kept open ~1s is read — arrow-keying past
        # mail never marks it (design 2026-07-15)
        self._dwell = QTimer(self)
        self._dwell.setSingleShot(True)
        self._dwell.setInterval(1000)
        self._dwell.timeout.connect(self._dwell_fired)
        self._dwell_mid = None
        self._last_sel = None
```

At the top of `populate()`:

```python
        if self.state.selected_mail != self._last_sel:
            self._last_sel = self.state.selected_mail
            self._dwell.stop()
            sel = self.state.sel_mail()
            if sel is not None and sel["unread"]:
                self._dwell_mid = sel["id"]
                self._dwell.start()
        self._build_chips()
```

New methods:

```python
    def _build_chips(self):
        clear_layout(self.chips_lay)
        for name, scope in ([("All", "all"), ("Unread", "unread")]
                            + [(l, l) for l in self.state.mail_labels]):
            color = (T.TEXT_SECONDARY if scope in ("all", "unread")
                     else T.label_color(name))
            sel = self.state.mail_scope == scope
            self.chips_lay.addWidget(ClickChip(
                name, color, color, bg=(color + "1f" if sel else None),
                on_click=lambda s=scope: self.state.set_mail_scope(s)))

    def _dwell_fired(self):
        if self._dwell_mid and self.state.selected_mail == self._dwell_mid:
            self.state.auto_read(self._dwell_mid)

    def _suggest(self):
        self.suggest_btn.setEnabled(False)
        self.state.suggest_labels(
            lambda _r: self.suggest_btn.setEnabled(True))
```

Row pills — in the `populate()` message loop, after the preview `ElideLabel`:

```python
            names = m.get("label_names") or []
            sug = self.state.mail_suggestions.get(m["id"])
            if names or sug:
                pills = hbox(s=4)
                for n in names[:3]:
                    c = T.label_color(n)
                    pills.addWidget(Chip(n, c, c, px=9, radius=7, hpad=6, vpad=1))
                if sug:
                    pills.addWidget(ClickChip(
                        f"＋ {sug}", T.ACCENT, T.ACCENT, px=9,
                        on_click=lambda mid=m["id"]: self.state.apply_suggestion(mid),
                        tooltip="Suggested label — click to file it (leaves the inbox)"))
                pills.addStretch(1)
                body.addSpacing(3)
                body.addLayout(pills)
```

Reading pane — after the sender row separator, add pills + the rule button next to the archive/read buttons:

```python
        # in _populate_pane, after read_btn:
        rule_btn = button("⚑ Rule", "outline", px=11)
        rule_btn.setFixedHeight(29)
        rule_btn.setToolTip("Always label mail like this…")
        rule_btn.clicked.connect(lambda: self.state.open_rule_editor(
            {"from_addrs": [m["from_addr"]] if m.get("from_addr") else []}))
        sl.addWidget(rule_btn)
```

```python
        # after the separator, before attachments:
        names = m.get("label_names") or []
        if names:
            pr = hbox(m=(0, 10, 0, 0), s=5)
            for n in names:
                c = T.label_color(n)
                pr.addWidget(Chip(n, c, c, px=10, radius=7, hpad=7, vpad=2))
            pr.addStretch(1)
            self.pane_lay.addLayout(pr)
```

- [ ] **Step 4: Run UI suite** → PASS.
- [ ] **Step 5: Commit** — `Mail screen: filter chips, label pills, dwell auto-read, suggest + rule buttons` + trailer.

---

### Task 11: Rule editor dialog + Settings rules section

**Files:**
- Create: `lumen/ui_v2/rule_dialog.py`
- Modify: `lumen/ui_v2/main.py` (instantiate + connect `rule_edit_requested`), `lumen/ui_v2/screens/settings.py` ([mail_rules] section)
- Test: `tests/ui/test_ui_v2.py` / `tests/ui/test_settings_live.py` (follow local convention)

**Interfaces:**
- Consumes: Task 9 state methods; ConfirmOverlay geometry pattern (`confirm.py`) for the overlay card.
- Produces: `RuleDialog(parent, state)` with `open(prefill: dict | None)` — prefill may carry `id` (edit mode) and any rule fields. Settings shows a `[mail_rules]` section: rule rows (colored label chip, matcher summary, enabled Switch, Edit, ✕) + "＋ New rule".

- [ ] **Step 1: Failing tests:** RuleDialog `open({"from_addrs": ["a@b.com"]})` prefills the From field; Save with an empty label shows the hint and does not call `create_rule`; Save with a label + condition calls `create_rule` with cleaned lists; Settings screen builds with an empty rules box showing the "No rules yet" line.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** `rule_dialog.py` (overlay card modeled on `ConfirmOverlay`):

```python
"""Mail-rule editor overlay: label + any-of matchers. Save hands the rule to
rules.create/update — creation still passes the daemon's confirm gate (with
the apply-to-existing checkbox), so this popup never writes to Gmail itself."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QFrame, QLineEdit, QWidget

from . import theme as T
from .widgets import (ClickChip, FlowLayout, button, clear_layout, hbox,
                      hline, label, qcolor, vbox)


def _split(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


class RuleDialog(QWidget):
    """Dimmed overlay with a centered 480px editor card."""

    def __init__(self, parent: QWidget, state):
        super().__init__(parent)
        self.state = state
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._rid = None

        lay = hbox(self, (20, 20, 20, 20), 0)
        lay.addStretch(1)
        wrap = vbox(s=0)
        wrap.addStretch(1)
        self.card = QFrame()
        self.card.setProperty("cls", "dialog")
        self.card.setFixedWidth(480)
        self.card_lay = vbox(self.card, (18, 16, 18, 16), 8)
        wrap.addWidget(self.card)
        wrap.addStretch(1)
        lay.addLayout(wrap)
        lay.addStretch(1)
        self.hide()

    def open(self, prefill: dict | None = None):
        p = prefill or {}
        self._rid = p.get("id")
        self._build(p)
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.label_edit.setFocus()

    def _field(self, caption: str, value: str, placeholder: str) -> QLineEdit:
        self.card_lay.addWidget(label(caption, 11, T.TEXT_DIM))
        edit = QLineEdit(value)
        edit.setPlaceholderText(placeholder)
        f = edit.font()
        f.setPixelSize(12)
        edit.setFont(f)
        self.card_lay.addWidget(edit)
        return edit

    def _build(self, p: dict):
        clear_layout(self.card_lay)
        self.card_lay.addWidget(label(
            "Edit mail rule" if self._rid else "New mail rule",
            14, T.TEXT_PRIMARY, 600))
        self.card_lay.addWidget(label(
            "Matching mail gets the label and leaves the inbox — any "
            "condition is enough.", 11, T.TEXT_DIM, wrap=True))
        self.card_lay.addWidget(hline(T.BORDER_MED))
        self.label_edit = self._field("label", p.get("label", ""), "e.g. Bills")
        if self.state.mail_labels:
            chips_host = QWidget()
            chips = FlowLayout(chips_host)
            for name in self.state.mail_labels:
                c = T.label_color(name)
                chips.addWidget(ClickChip(
                    name, c, c, px=9,
                    on_click=lambda n=name: self.label_edit.setText(n)))
            self.card_lay.addWidget(chips_host)
        self.from_edit = self._field("from (comma-separated)",
                                     ", ".join(p.get("from_addrs", [])),
                                     "sender@example.com")
        self.domain_edit = self._field("domains", ", ".join(p.get("domains", [])),
                                       "example.com")
        self.subj_edit = self._field("subject keywords",
                                     ", ".join(p.get("subject_kw", [])),
                                     "invoice, statement")
        self.body_edit = self._field("body keywords",
                                     ", ".join(p.get("body_kw", [])), "")
        self.hint = label("", 11, T.WARN)
        self.card_lay.addWidget(self.hint)
        foot = hbox(m=(0, 8, 0, 0), s=10)
        cancel = button("Cancel", "cancel", 12, 34)
        cancel.clicked.connect(self.hide)
        foot.addWidget(cancel, 1)
        save = button("Save rule", "confirm", 12, 34)
        save.clicked.connect(self._save)
        foot.addWidget(save, 1)
        self.card_lay.addLayout(foot)

    def _save(self):
        rule = {"label": self.label_edit.text().strip(),
                "from_addrs": _split(self.from_edit.text()),
                "domains": _split(self.domain_edit.text()),
                "subject_kw": _split(self.subj_edit.text()),
                "body_kw": _split(self.body_edit.text())}
        if not rule["label"] or not any(
                rule[c] for c in ("from_addrs", "domains", "subject_kw", "body_kw")):
            self.hint.setText("needs a label and at least one condition")
            return
        done = lambda r: self.state.toast_requested.emit(
            ("✓ " if r.get("ok") else "") + r.get("message", "Saved."))
        if self._rid is None:
            self.state.create_rule(rule, done)
        else:
            self.state.update_rule(self._rid, rule, done)
        self.hide()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor("#06070b", int(0.72 * 255)))
```

`main.py`: `from .rule_dialog import RuleDialog`; after `self.compose = …`: `self.rule_dialog = RuleDialog(self, self.state)`; connect: `self.state.rule_edit_requested.connect(self._open_rule_editor)` with:

```python
    def _open_rule_editor(self, prefill: dict):
        self.show()
        self.raise_()
        self.activateWindow()
        self.rule_dialog.open(prefill)
```

`settings.py` — insert BEFORE the `[memory]` section (imports: add `Switch`, `ClickLabel`, `Chip` to the widgets import):

```python
        # [mail_rules] — deterministic inbox rules (Phase 12)
        v.addWidget(label("[mail_rules]", 12, T.ACCENT))
        v.addSpacing(8)
        v.addWidget(label("New mail matching a rule is labeled and leaves the "
                          "inbox — automatic, no model involved.", 11, T.TEXT_DIM))
        v.addSpacing(8)
        new_rule = button("＋ New rule", "ghost", 12, 30)
        new_rule.clicked.connect(lambda: self.state.open_rule_editor())
        nr = hbox(s=8)
        nr.addWidget(new_rule)
        nr.addStretch(1)
        v.addLayout(nr)
        v.addSpacing(12)
        self._rules_box = QWidget()
        vbox(self._rules_box, (0, 0, 0, 0), 6)
        v.addWidget(self._rules_box)
        v.addSpacing(20)
```

`showEvent` adds `self.state.list_rules(self._on_rules)`. New methods:

```python
    def _on_rules(self, result: dict):
        self._rules = (result or {}).get("rules", [])
        box = self._rules_box.layout()
        clear_layout(box)
        if not self._rules:
            box.addWidget(label("No rules yet — say “create a rule…” in chat, "
                                "or use ＋ New rule.", 11, T.TEXT_FAINT))
            return
        for r in self._rules:
            box.addWidget(self._rule_row(r))

    @staticmethod
    def _rule_summary(r: dict) -> str:
        parts = []
        for key, name in (("from_addrs", "from"), ("domains", "domain"),
                          ("subject_kw", "subject"), ("body_kw", "body")):
            if r[key]:
                parts.append(f"{name}: {', '.join(r[key][:3])}")
        return " · ".join(parts)

    def _rule_row(self, r: dict) -> QWidget:
        row = QWidget()
        rl = hbox(row, (12, 8, 12, 8), 10)
        c = T.label_color(r["label"])
        rl.addWidget(Chip(r["label"], c, c, px=10, radius=7, hpad=7, vpad=2))
        rl.addWidget(label(self._rule_summary(r), 11, T.TEXT_DIM), 1)
        sw = Switch(r["enabled"])
        sw.clicked.connect(lambda _=False, rid=r["id"], s=sw:
                           self.state.toggle_rule(rid, s.isChecked()))
        rl.addWidget(sw)
        edit = button("Edit", "ghost", 11, 26)
        edit.clicked.connect(lambda _=False, rr=r: self.state.open_rule_editor(rr))
        rl.addWidget(edit)
        rl.addWidget(ClickLabel(
            "✕", 12, T.TEXT_FAINT,
            on_click=lambda rid=r["id"]: self.state.delete_rule(
                rid, lambda _r: self.state.list_rules(self._on_rules)),
            tooltip="Delete rule"))
        return row
```

- [ ] **Step 4: Run UI suite** → PASS.
- [ ] **Step 5: Commit** — `Rule editor dialog + Settings [mail_rules] section` + trailer.

---

### Task 12: Docs, full suite, refresh-button verify pass, live verify

**Files:**
- Modify: `.claude/skills/email-menu.md`, `new-features.md`, `.claude/skills/development-plan.md`
- No code changes expected (§ A is verify-only).

- [ ] **Step 1: § A refresh verify.** Confirm in code: `MailScreen` ↻ → `state.refresh_inbox()` → `mail.refresh` → `sync_once()` + scoped reload → `_status_text()` shows "syncing —…/synced <time>". Covered by router + UI tests; fix anything found broken (none expected).

- [ ] **Step 2: Update `email-menu.md`:**
  - Decided gates: replace the "browsing never changes read state" bullet with the 2026-07-15 reversal (1s dwell auto-read → Gmail; read-state writes ungated; archive still confirms; labeling = moving — applying a label removes INBOX; v1 manage actions now include label-apply and rules).
  - New "Labels & rules (2026-07-15)" section: `gmail_labels` map cached on every sync; `mail_rules` table; rules run deterministically in `_incremental` only (never bulk/re-baseline — mass-modify hazard); rule writes pre-authorized at creation via the confirm-with-checkbox; suggest-labels is per-message on explicit press (triage batch lesson); "unread" queries are INBOX-scoped now.

- [ ] **Step 3: Update `new-features.md`:** mark §§ A–E with `**Built 2026-07-15.**` lines (keep the parking lot).

- [ ] **Step 4: Update `development-plan.md`:** append a Phase 12 (Inbox sorting & rules) entry following the file's existing format, marked DONE with date.

- [ ] **Step 5: Full suite:** `python -m pytest tests -q` (UI part offscreen). Expected: all pass.

- [ ] **Step 6: Live verify** via the project `verify` skill (drive the daemon over its socket; offscreen screenshots of Mail + Settings showing chips, pills, rules section).

- [ ] **Step 7: Final commit** — `Close out inbox sorting/rules batch: docs + verify` + trailer. Do NOT push (user pushes).

---

## Self-Review Notes

- **Spec coverage:** A (Task 12 verify), B (Tasks 5, 10), C (Tasks 1, 5, 9, 10), D storage/engine (Tasks 1–3), D.1 (Task 3), D.2 paths 1/2/3 (Tasks 7, 10, 11 — all converge on `_gated_rule_save`), D.3 (Task 7), E (Tasks 8–10), doc gates (Task 12). Out-of-scope items untouched.
- **Deviations from spec text (implementation-level, with rationale):** per-message suggest calls instead of one batched prompt (triage live lesson 2026-07-13; same user-visible contract); `emails.mark_read` ungated (consistency with the locked auto-read decision); "Unread" scope change applies to `unread()` consumers too (labeled mail has left the inbox — spec's own model).
- **Type consistency check:** `label_id` (str Gmail id) vs `rid` (int rule id) kept distinct; `rule` dict shape identical across RuleStore/validate_rule/UI (`label` + 4 list cols); `label_names` (names) vs `labels` (ids) never mixed.
