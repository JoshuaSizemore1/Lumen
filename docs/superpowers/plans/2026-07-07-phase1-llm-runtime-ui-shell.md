# Phase 1: LLM Runtime + Daemon Skeleton + UI Shell — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working end-to-end loop — question typed into the Lumen launcher → asyncio daemon → Ollama → streamed answer — plus a styled six-screen PyQt6 shell and a complete Ollama setup walkthrough, with the model demonstrably unloading from RAM after idle.

**Architecture:** Two processes. The daemon (asyncio) owns all LLM access: Unix-socket JSON-lines IPC → pass-through router → httpx streaming client for Ollama with `keep_alive` on every request. The UI (PyQt6) renders a tabbed shell with six static screens from the design mockups; only the launcher talks to the daemon (via `QLocalSocket`). Spec: `docs/superpowers/specs/2026-07-07-phase1-llm-runtime-ui-shell-design.md`.

**Tech Stack:** Python 3.12+, uv + hatchling, httpx, PyQt6 (QtCore/QtGui/QtWidgets/QtNetwork), pytest + pytest-asyncio + pytest-qt, Ollama (`qwen3:4b`), systemd user units, Hyprland bind for the global hotkey.

## Global Constraints

- Python ≥ 3.12 (`tomllib` is stdlib). Runtime deps: `PyQt6`, `httpx` only. Dev deps: `pytest`, `pytest-asyncio`, `pytest-qt`.
- Run everything through uv: `uv run pytest`, `uv run lumen-daemon`, `uv run lumen-ui`.
- Every commit message ends with `This commit used N prompts.` — N counts user prompts since the last push. **At plan time N = 3**; if the user pushes or sends new prompts during execution, recompute (prompts since last push, inclusive of the latest).
- NEVER add a `Co-Authored-By` or any Claude/Anthropic credit line to commits.
- All LLM traffic goes through `lumen/daemon/llm/` — the UI never talks to Ollama.
- The UI holds no business logic: render, collect input, confirm. Nothing else.
- Idle-unload is never disabled: `keep_alive` is sent on **every** Ollama request; never `-1`.
- Qt tests run headless: `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen` (Task 8).
- Design fidelity target this phase: correct layout grids, tokens, and placeholder content from the mockups in `.claude/lumenFrontEndUIReference/design/` — pixel-parity polish is Phase 10, don't chase it now.
- Colors/type come from `lumen/ui/theme.py` constants only — no hex literals inside screen files (per-datum colors like tag chips take the constant as an argument).
- Qt QSS does not support `letter-spacing`, `box-shadow`, or `text-overflow` — do not write them into stylesheets.

---

### Task 1: Packaging bootstrap

**Files:**
- Create: `pyproject.toml`
- Modify: `.gitignore` (append uv/venv entries if missing)

**Interfaces:**
- Produces: installable `lumen` package; console scripts `lumen-daemon`, `lumen-ui` (entry-point functions land in Tasks 6 and 19); `uv run pytest` works.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "lumen"
version = "0.1.0"
description = "Local-first daily assistant"
requires-python = ">=3.12"
dependencies = [
    "PyQt6>=6.6",
    "httpx>=0.27",
]

[project.scripts]
lumen-daemon = "lumen.daemon.__main__:main"
lumen-ui = "lumen.ui.__main__:main"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "pytest-qt>=4.4",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["lumen"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Ensure `.gitignore` covers the venv and uv artifacts**

Append (only the lines not already present):

```
.venv/
uv.lock
__pycache__/
*.pyc
```

Check first: `cat .gitignore`. (If the project wants `uv.lock` committed later, that's a deliberate future change — for a single-machine personal app, ignoring it is fine.)

- [ ] **Step 3: Sync and verify the environment**

Run: `cd /home/josh/Projects/Lumen && uv sync`
Expected: creates `.venv`, installs PyQt6 + httpx + dev group, exit 0.

Run: `uv run python -c "import lumen, httpx, PyQt6; print('ok')"`
Expected: `ok`

Run: `uv run pytest`
Expected: `no tests ran` (exit code 5 — stub test files contain only comments; that's fine at this point).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .gitignore
git commit -m "Add packaging: pyproject with uv, console script entry points

This commit used 3 prompts."
```

---

### Task 2: Daemon config

**Files:**
- Create: `tests/daemon/test_config.py`
- Modify: `lumen/daemon/config.py` (replace stub comment)
- Modify: `lumen/config.example.toml` (replace stub comment)

**Interfaces:**
- Produces:
  - `Config` frozen dataclass: `model: str`, `idle_unload_minutes: int`, `ollama_url: str`, `socket_path: pathlib.Path`; property `keep_alive: str` (e.g. `"10m"`).
  - `load_config(path: Path | None = None) -> Config` — reads TOML; missing file or missing keys → defaults.
  - `default_socket_path() -> Path` — `$XDG_RUNTIME_DIR/lumen/daemon.sock`, falling back to `/tmp/lumen-<uid>/daemon.sock`.

- [ ] **Step 1: Write the failing tests**

`tests/daemon/test_config.py`:

```python
from pathlib import Path

from lumen.daemon.config import Config, default_socket_path, load_config


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.model == "qwen3:4b"
    assert cfg.idle_unload_minutes == 10
    assert cfg.ollama_url == "http://127.0.0.1:11434"
    assert cfg.socket_path == default_socket_path()


def test_reads_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[llm]\nmodel = "gemma3:12b-it-qat"\nidle_unload_minutes = 5\n'
        'ollama_url = "http://127.0.0.1:9999"\n'
        f'[ipc]\nsocket_path = "{tmp_path}/d.sock"\n'
    )
    cfg = load_config(p)
    assert cfg.model == "gemma3:12b-it-qat"
    assert cfg.idle_unload_minutes == 5
    assert cfg.ollama_url == "http://127.0.0.1:9999"
    assert cfg.socket_path == Path(f"{tmp_path}/d.sock")


def test_keep_alive_format():
    assert Config().keep_alive == "10m"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'Config'`.

- [ ] **Step 3: Implement `lumen/daemon/config.py`**

```python
"""Daemon configuration. Non-secret settings from config.toml; secrets stay in .env."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def default_socket_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/lumen-{os.getuid()}"
    return Path(base) / "lumen" / "daemon.sock"


@dataclass(frozen=True)
class Config:
    model: str = "qwen3:4b"
    idle_unload_minutes: int = 10
    ollama_url: str = "http://127.0.0.1:11434"
    socket_path: Path = field(default_factory=default_socket_path)

    @property
    def keep_alive(self) -> str:
        return f"{self.idle_unload_minutes}m"


def load_config(path: Path | None = None) -> Config:
    path = path or Path(__file__).resolve().parent.parent / "config.toml"
    if not path.exists():
        return Config()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    llm = data.get("llm", {})
    ipc = data.get("ipc", {})
    kwargs = {}
    if "model" in llm:
        kwargs["model"] = llm["model"]
    if "idle_unload_minutes" in llm:
        kwargs["idle_unload_minutes"] = llm["idle_unload_minutes"]
    if "ollama_url" in llm:
        kwargs["ollama_url"] = llm["ollama_url"]
    if "socket_path" in ipc:
        kwargs["socket_path"] = Path(ipc["socket_path"])
    return Config(**kwargs)
```

- [ ] **Step 4: Write the real `lumen/config.example.toml`**

```toml
# Copy to config.toml (same directory) and adjust. Secrets go in .env, never here.

[llm]
model = "qwen3:4b"          # fast-path model; see .claude/skills/llm-serving.md for slots
idle_unload_minutes = 10    # model evicts from RAM after this much inactivity — never -1
ollama_url = "http://127.0.0.1:11434"

[ipc]
# socket_path = "/run/user/1000/lumen/daemon.sock"  # default: $XDG_RUNTIME_DIR/lumen/daemon.sock
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/test_config.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/daemon/test_config.py lumen/daemon/config.py lumen/config.example.toml
git commit -m "Add daemon config: TOML loading with defaults and keep_alive derivation

This commit used 3 prompts."
```

---

### Task 3: Ollama client

**Files:**
- Modify: `tests/daemon/llm/test_client.py` (replace stub comment)
- Modify: `lumen/daemon/llm/client.py` (replace stub comment)

**Interfaces:**
- Consumes: nothing from earlier tasks (base_url/model/keep_alive passed as plain args).
- Produces:
  - `class LLMUnavailable(Exception)`
  - `class OllamaClient:`
    - `__init__(self, base_url: str, model: str, keep_alive: str, transport: httpx.AsyncBaseTransport | None = None)`
    - `chat(self, messages: list[dict]) -> AsyncIterator[str]` — async generator of text chunks; raises `LLMUnavailable` if Ollama is unreachable.
    - `async unload(self) -> None` — immediate model eviction (`keep_alive: 0`).
    - `async aclose(self) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/daemon/llm/test_client.py`:

```python
import json

import httpx
import pytest

from lumen.daemon.llm.client import LLMUnavailable, OllamaClient


def ndjson(*objs) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


def make_client(handler) -> OllamaClient:
    return OllamaClient(
        "http://test", "qwen3:4b", "10m", transport=httpx.MockTransport(handler)
    )


async def test_chat_streams_chunks_and_sends_keep_alive():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "Hel"}, "done": False},
                {"message": {"content": "lo"}, "done": False},
                {"message": {"content": ""}, "done": True},
            ),
        )

    client = make_client(handler)
    chunks = [c async for c in client.chat([{"role": "user", "content": "hi"}])]
    assert chunks == ["Hel", "lo"]
    assert seen["keep_alive"] == "10m"          # the power constraint, enforced per-request
    assert seen["model"] == "qwen3:4b"
    assert seen["stream"] is True
    await client.aclose()


async def test_unload_sends_zero_keep_alive_and_empty_messages():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=ndjson({"done": True}))

    client = make_client(handler)
    await client.unload()
    assert seen["keep_alive"] == 0
    assert seen["messages"] == []
    await client.aclose()


async def test_unreachable_raises_llm_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = make_client(handler)
    with pytest.raises(LLMUnavailable):
        async for _ in client.chat([{"role": "user", "content": "hi"}]):
            pass
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/llm/test_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'LLMUnavailable'`.

- [ ] **Step 3: Implement `lumen/daemon/llm/client.py`**

```python
"""Ollama client. keep_alive rides on every request so the model idle-unloads
even if the service-level OLLAMA_KEEP_ALIVE override is lost. Never -1."""

import json
from collections.abc import AsyncIterator

import httpx


class LLMUnavailable(Exception):
    """Ollama is not reachable — the service is probably stopped."""


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        keep_alive: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url
        self.model = model
        self.keep_alive = keep_alive
        # No read timeout: generation on CPU can legitimately be slow.
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0),
        )

    async def chat(self, messages: list[dict]) -> AsyncIterator[str]:
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": self.keep_alive,
        }
        try:
            async with self._http.stream("POST", "/api/chat", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if data.get("done"):
                        return
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise LLMUnavailable(
                f"Ollama unreachable at {self.base_url} — is the service running? "
                "(systemctl --user status ollama)"
            ) from e

    async def unload(self) -> None:
        """Evict the model from RAM now (the 'sleep' command)."""
        try:
            await self._http.post(
                "/api/chat",
                json={"model": self.model, "messages": [], "keep_alive": 0},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            pass  # not running == nothing loaded == already "asleep"

    async def aclose(self) -> None:
        await self._http.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/llm/test_client.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/daemon/llm/test_client.py lumen/daemon/llm/client.py
git commit -m "Add Ollama client: streaming chat with enforced keep_alive, unload, error mapping

This commit used 3 prompts."
```

---

### Task 4: Router (Phase 1 pass-through)

**Files:**
- Modify: `tests/daemon/test_router.py` (replace stub comment)
- Modify: `lumen/daemon/router.py` (replace stub comment)

**Interfaces:**
- Consumes: `OllamaClient.chat(messages) -> AsyncIterator[str]`, `OllamaClient.unload()`, `LLMUnavailable` from Task 3.
- Produces: `class Router:` with `__init__(self, llm)` and `handle(self, type_: str, payload: dict) -> AsyncIterator[dict]` yielding dicts shaped `{"chunk": str}` | `{"done": True}` | `{"error": str}`. Request types: `"chat"` (payload `{"message": str}`) and `"sleep"` (payload `{}`).

- [ ] **Step 1: Write the failing tests**

`tests/daemon/test_router.py`:

```python
from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.router import Router


class FakeLLM:
    def __init__(self, chunks=("a", "b"), fail=False):
        self._chunks = chunks
        self._fail = fail
        self.unloaded = False

    async def chat(self, messages):
        if self._fail:
            raise LLMUnavailable("down")
        for c in self._chunks:
            yield c

    async def unload(self):
        self.unloaded = True


async def collect(router, type_, payload):
    return [r async for r in router.handle(type_, payload)]


async def test_chat_streams_then_done():
    out = await collect(Router(FakeLLM()), "chat", {"message": "hi"})
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]


async def test_chat_llm_down_yields_error():
    out = await collect(Router(FakeLLM(fail=True)), "chat", {"message": "hi"})
    assert len(out) == 1 and "down" in out[0]["error"]


async def test_sleep_unloads():
    llm = FakeLLM()
    out = await collect(Router(llm), "sleep", {})
    assert llm.unloaded and out == [{"done": True}]


async def test_unknown_type_errors():
    out = await collect(Router(FakeLLM()), "frobnicate", {})
    assert "unknown" in out[0]["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_router.py -v`
Expected: FAIL — `ImportError: cannot import name 'Router'`.

- [ ] **Step 3: Implement `lumen/daemon/router.py`**

```python
"""Request router. Phase 1: pass-through to the LLM ("chat") plus "sleep".
Tool-call vs direct-answer classification arrives with the connector phases."""

from collections.abc import AsyncIterator

from lumen.daemon.llm.client import LLMUnavailable


class Router:
    def __init__(self, llm):
        self._llm = llm

    async def handle(self, type_: str, payload: dict) -> AsyncIterator[dict]:
        if type_ == "chat":
            messages = [{"role": "user", "content": payload.get("message", "")}]
            try:
                async for chunk in self._llm.chat(messages):
                    yield {"chunk": chunk}
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            yield {"done": True}
        elif type_ == "sleep":
            await self._llm.unload()
            yield {"done": True}
        else:
            yield {"error": f"unknown request type: {type_}"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/test_router.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/daemon/test_router.py lumen/daemon/router.py
git commit -m "Add router: chat pass-through, sleep, and error surfacing

This commit used 3 prompts."
```

---

### Task 5: IPC server

**Files:**
- Create: `tests/daemon/test_ipc.py`
- Modify: `lumen/daemon/ipc_server.py` (replace stub comment)

**Interfaces:**
- Consumes: `Router.handle(type_, payload) -> AsyncIterator[dict]` from Task 4.
- Produces: `class IPCServer:` with `__init__(self, socket_path: Path, router)`, `async start(self) -> None`, `async stop(self) -> None`. Wire protocol: client sends one JSON object per line `{"id": int, "type": str, "payload": dict}`; server responds with the router's dicts, each with `"id"` added, one per line. Malformed lines are logged and skipped. Connections are persistent (many requests per connection).

- [ ] **Step 1: Write the failing tests**

`tests/daemon/test_ipc.py`:

```python
import asyncio
import json

import pytest

from lumen.daemon.ipc_server import IPCServer


class FakeRouter:
    async def handle(self, type_, payload):
        if type_ == "chat":
            yield {"chunk": f"echo:{payload['message']}"}
            yield {"done": True}
        else:
            yield {"error": "unknown request type: " + type_}


@pytest.fixture
async def server(tmp_path):
    srv = IPCServer(tmp_path / "d.sock", FakeRouter())
    await srv.start()
    yield srv
    await srv.stop()


async def send_line(tmp_path, *lines: bytes) -> list[dict]:
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
    for line in lines:
        writer.write(line + b"\n")
    await writer.drain()
    out = []
    while True:
        raw = await asyncio.wait_for(reader.readline(), timeout=2)
        msg = json.loads(raw)
        out.append(msg)
        if "done" in msg or "error" in msg:
            break
    writer.close()
    await writer.wait_closed()
    return out


async def test_round_trip(server, tmp_path):
    out = await send_line(
        tmp_path, json.dumps({"id": 7, "type": "chat", "payload": {"message": "hi"}}).encode()
    )
    assert out == [{"id": 7, "chunk": "echo:hi"}, {"id": 7, "done": True}]


async def test_malformed_line_skipped_then_valid_line_served(server, tmp_path):
    out = await send_line(
        tmp_path,
        b"this is not json",
        json.dumps({"id": 1, "type": "chat", "payload": {"message": "ok"}}).encode(),
    )
    assert out[-1] == {"id": 1, "done": True}


async def test_socket_created_with_private_dir(server, tmp_path):
    assert (tmp_path / "d.sock").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_ipc.py -v`
Expected: FAIL — `ImportError: cannot import name 'IPCServer'`.

- [ ] **Step 3: Implement `lumen/daemon/ipc_server.py`**

```python
"""Unix-socket IPC. One JSON object per line in, router dicts (with id) per line out.
The UI is the only expected client; connections are persistent."""

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class IPCServer:
    def __init__(self, socket_path: Path, router):
        self._path = Path(socket_path)
        self._router = router
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path.unlink(missing_ok=True)  # stale socket from a previous run
        self._server = await asyncio.start_unix_server(self._handle, path=str(self._path))

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self._path.unlink(missing_ok=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while raw := await reader.readline():
                try:
                    req = json.loads(raw)
                    req_id, type_, payload = req["id"], req["type"], req.get("payload", {})
                except (json.JSONDecodeError, KeyError, TypeError):
                    log.warning("skipping malformed IPC line: %r", raw[:200])
                    continue
                async for resp in self._router.handle(type_, payload):
                    resp["id"] = req_id
                    writer.write(json.dumps(resp).encode() + b"\n")
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass  # UI went away mid-stream; nothing to do
        finally:
            writer.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/test_ipc.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/daemon/test_ipc.py lumen/daemon/ipc_server.py
git commit -m "Add IPC server: persistent unix-socket JSON-lines with malformed-line tolerance

This commit used 3 prompts."
```

---

### Task 6: Daemon entrypoint

**Files:**
- Create: `lumen/daemon/__main__.py`

**Interfaces:**
- Consumes: `load_config()` (Task 2), `OllamaClient` (Task 3), `Router` (Task 4), `IPCServer` (Task 5).
- Produces: `main() -> None` (sync wrapper for the `lumen-daemon` console script) and `python -m lumen.daemon`.

- [ ] **Step 1: Implement `lumen/daemon/__main__.py`**

```python
"""Daemon entrypoint: config -> Ollama client -> router -> IPC server, until SIGINT/SIGTERM."""

import asyncio
import logging
import signal

from lumen.daemon.config import load_config
from lumen.daemon.ipc_server import IPCServer
from lumen.daemon.llm.client import OllamaClient
from lumen.daemon.router import Router

log = logging.getLogger("lumen.daemon")


async def run() -> None:
    cfg = load_config()
    llm = OllamaClient(cfg.ollama_url, cfg.model, cfg.keep_alive)
    server = IPCServer(cfg.socket_path, Router(llm))
    await server.start()
    log.info("listening on %s (model=%s, keep_alive=%s)", cfg.socket_path, cfg.model, cfg.keep_alive)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("shutting down")
    await server.stop()
    await llm.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Manual smoke test (Ollama not yet installed — that's the point)**

Run in background: `uv run lumen-daemon &` then:

```bash
python3 - <<'EOF'
import json, socket, os
p = (os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/lumen-{os.getuid()}") + "/lumen/daemon.sock"
s = socket.socket(socket.AF_UNIX); s.connect(p)
s.sendall(json.dumps({"id": 1, "type": "chat", "payload": {"message": "hi"}}).encode() + b"\n")
print(s.recv(4096).decode())
EOF
```

Expected: one JSON line with `"error"` containing `Ollama unreachable` — the daemon survives Ollama being down and says so cleanly. Kill the daemon (`kill %1`).

- [ ] **Step 3: Run the full daemon test suite**

Run: `uv run pytest tests/daemon -v`
Expected: all passing (config 3, client 3, router 4, ipc 3).

- [ ] **Step 4: Commit**

```bash
git add lumen/daemon/__main__.py
git commit -m "Add daemon entrypoint wiring config, client, router, and IPC server

This commit used 3 prompts."
```

---

### Task 7: Ollama install + setup walkthrough

**Files:**
- Create: `docs/ollama-setup.md`

**Interfaces:**
- Produces: Ollama running as a systemd user service with `OLLAMA_KEEP_ALIVE=10m`; `qwen3:4b` pulled; the complete walkthrough doc. This task touches the system — **`sudo` steps may require the user**; if a sudo command fails for lack of a TTY/password, stop and ask the user to run that exact command, then continue.

- [ ] **Step 1: Write `docs/ollama-setup.md`** with exactly this content:

````markdown
# Ollama setup — Lumen

Written for this machine: Arch Linux, Hyprland/Wayland, Intel Core Ultra 9 285H,
32GB RAM, Intel Arc 140T iGPU (no discrete GPU). Inference is CPU-based; see
"iGPU acceleration" at the bottom before trying to change that.

## 1. Install

```bash
sudo pacman -S ollama          # the runtime
sudo pacman -S ttf-ibm-plex    # IBM Plex Sans — Lumen's prose font (JetBrains Mono is already installed)
```

Arch ships a **system** service (`/usr/lib/systemd/system/ollama.service`). Lumen
runs Ollama as a **user** service instead (per `.claude/skills/llm-serving.md`) so
it lives and dies with your session and the keep-alive override is per-user.
Make sure the system one stays off:

```bash
sudo systemctl disable --now ollama.service 2>/dev/null || true
```

## 2. User service with idle-unload

Create `~/.config/systemd/user/ollama.service`:

```ini
[Unit]
Description=Ollama (Lumen local LLM)

[Service]
ExecStart=/usr/bin/ollama serve
Environment="OLLAMA_KEEP_ALIVE=10m"
Restart=on-failure

[Install]
WantedBy=default.target
```

`OLLAMA_KEEP_ALIVE=10m` = the model unloads from RAM after 10 idle minutes.
Never set it to `-1`. The Lumen daemon *also* sends `keep_alive` on every
request (from `idle_unload_minutes` in `config.toml`), so idle-unload holds even
if this file is lost.

```bash
systemctl --user daemon-reload
systemctl --user enable --now ollama
systemctl --user status ollama --no-pager   # expect: active (running)
```

## 3. Pull the fast-path model

```bash
ollama pull qwen3:4b       # ~2.6GB download
curl -s localhost:11434/api/tags | python3 -m json.tool   # should list qwen3:4b
```

Model slots (decided 2026-07-07, see `.claude/skills/llm-serving.md`):

| Slot | Model | Status |
|---|---|---|
| Fast path / router / tools | `qwen3:4b` | pull now |
| Tool-chain escalation | Qwen3 14B-class | Phase 3 — do NOT pull yet |
| Writing escalation candidate | `gemma3:12b-it-qat` | Phase 7 benchmark — do NOT pull yet |

## 4. Start / stop / call — the control surface

| Action | How |
|---|---|
| Start the server | `systemctl --user start ollama` (auto-starts on login once enabled) |
| Stop the server | `systemctl --user stop ollama` |
| Load the model | automatic, on the first request (cold start = a few seconds) |
| Call the model | only ever through the Lumen daemon (`daemon/llm/client.py`) |
| Unload NOW ("sleep") | tray menu → "Sleep model now", or: `curl localhost:11434/api/chat -d '{"model":"qwen3:4b","messages":[],"keep_alive":0}'` |
| What's loaded? | `ollama ps` |

## 5. Verify idle-unload actually works (Phase 1 success criterion)

```bash
ollama run qwen3:4b "say hi"   # loads the model
ollama ps                      # shows qwen3:4b resident, with an UNTIL column
```

For a fast check, restart the user service with a 1-minute override, ask once,
and watch it evict:

```bash
systemctl --user edit ollama   # drop-in: [Service] Environment="OLLAMA_KEEP_ALIVE=1m"
systemctl --user restart ollama
ollama run qwen3:4b "say hi" && sleep 75 && ollama ps   # expect: empty table
```

Then delete the drop-in (`systemctl --user revert ollama`) and restart to go
back to 10m. Watch RAM live with `watch -n5 free -h` if you want to see the
gigabytes come and go.

## 6. Lumen daemon as a user service (optional, once Phase 1 works)

Create `~/.config/systemd/user/lumen-daemon.service`:

```ini
[Unit]
Description=Lumen daemon
After=ollama.service

[Service]
ExecStart=%h/Projects/Lumen/.venv/bin/lumen-daemon
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now lumen-daemon
```

During development, just run `uv run lumen-daemon` in a terminal instead.

## 7. Global hotkey (Hyprland)

Add to `~/.config/hypr/hyprland.conf` (pick any free bind):

```
bind = SUPER, SPACE, exec, ~/Projects/Lumen/.venv/bin/lumen-ui --toggle-launcher
```

`lumen-ui` is single-instance: if it's already running, the flag toggles the
launcher overlay in the running process; otherwise it starts and shows it.

## 8. iGPU acceleration (later, optional)

Stock Ollama has no Intel Arc backend; CPU inference on the 285H is fine for
4B-Q4. If cold-start or tokens/sec disappoint later, the options are Ollama's
experimental Vulkan build or llama.cpp's SYCL/Vulkan backends — benchmark
before adopting, and treat it as a Phase 11 experiment, not a dependency.

## Troubleshooting

- `curl localhost:11434` refused → `systemctl --user status ollama`, check `journalctl --user -u ollama -n 50`.
- Launcher says "daemon offline" → the *Lumen daemon* isn't running (section 6) — that's separate from Ollama.
- First answer after a quiet period is slow → that's the cold load; the launcher shows "waking model…". Expected, not a bug.
- Model never unloads → check nothing set `OLLAMA_KEEP_ALIVE=-1`; per-request keep_alive comes from `idle_unload_minutes` in `config.toml`.
````

- [ ] **Step 2: Execute the walkthrough top to bottom**

Run each command from sections 1–3 in order (`sudo` steps may need the user). Then verify:

Run: `curl -s localhost:11434/api/tags | grep -o 'qwen3:4b'`
Expected: `qwen3:4b`

- [ ] **Step 3: Live end-to-end daemon check**

Start `uv run lumen-daemon &`, re-run the socket snippet from Task 6 Step 2.
Expected: `{"id": 1, "chunk": ...}` lines forming a real answer, ending `{"id": 1, "done": true}`. First run takes a few seconds (cold load). Kill the daemon after.

- [ ] **Step 4: Verify idle-unload** per doc section 5 (the 1-minute override variant).
Expected: `ollama ps` empty after the wait. Revert the override.

- [ ] **Step 5: Commit**

```bash
git add docs/ollama-setup.md
git commit -m "Add Ollama setup walkthrough: user service, keep-alive, model slots, verification

This commit used 3 prompts."
```

---

### Task 8: Theme + shared widgets

**Files:**
- Create: `lumen/ui/theme.py`, `lumen/ui/widgets.py`, `tests/conftest.py`, `tests/ui/test_theme.py`

**Interfaces:**
- Produces:
  - `theme.py` constants (single source of truth — screens import these, never hex literals): `BG_APP="#0d0e14"`, `BG_WINDOW="#1a1b26"`, `BG_CHROME="#16161e"`, `BG_OVERLAY="#101018"`, `BG_PANEL="#171821"`, `BG_PANEL_ALT="#14151d"`, `BG_FIELD="#0f1017"`, `BG_INSET="#12131b"`, `BG_WAYBAR="#0c0d13"`, `BORDER_STRONG="#2a2f45"`, `BORDER_MED="#262838"`, `BORDER_SOFT="#20222e"`, `BORDER_FAINT="#1a1c26"`, `TEXT_PRIMARY="#c0caf5"`, `TEXT_SECONDARY="#a9b1d6"`, `TEXT_MUTED="#787c99"`, `TEXT_DIM="#565f89"`, `TEXT_FAINT="#3b4261"`, `ACCENT="#7aa2f7"`, `ACCENT_SOFT="rgba(122,162,247,0.12)"`, `ACCENT_MID="rgba(122,162,247,0.20)"`, `OK="#9ece6a"`, `WARN="#e0af68"`, `NOW="#f7768e"`, `INFO="#7dcfff"`, `BOOK="#bb9af7"`, `BOOK_DIM="#8f83ac"`, `TAG_NEW="#ff9e64"`, `FONT_MONO="JetBrains Mono"`, `FONT_SANS="IBM Plex Sans"`, `TAG_COLORS={"work": ACCENT, "personal": BOOK, "home": OK, "admin": WARN, "tinker": INFO, "new": TAG_NEW}`.
  - `build_qss(accent: str = ACCENT) -> str` — app-wide stylesheet keyed on `role`/`kind` dynamic properties.
  - `widgets.py`: `label(text, role, wrap=False) -> QLabel` · `chip(text, color) -> QLabel` · `Panel(role="panel") -> QFrame subclass` · `button(text, kind) -> QPushButton` (kinds: `primary`, `ghost`, `link`, `tab`).

- [ ] **Step 1: Create `tests/conftest.py`** (headless Qt for the whole suite):

```python
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
```

- [ ] **Step 2: Write the failing test**

`tests/ui/test_theme.py`:

```python
from lumen.ui import theme
from lumen.ui.widgets import Panel, button, chip, label


def test_qss_threads_accent_and_fonts():
    qss = theme.build_qss("#9ece6a")
    assert "#9ece6a" in qss
    assert theme.ACCENT not in qss          # accent fully swapped, not appended
    assert theme.FONT_MONO in qss and theme.FONT_SANS in qss


def test_widget_factories_set_style_properties(qtbot):
    lab = label("hello", "eyebrow")
    assert lab.property("role") == "eyebrow"
    c = chip("work", theme.TAG_COLORS["work"])
    assert c.property("role") == "chip"
    p = Panel()
    assert p.property("role") == "panel"
    b = button("Go", "primary")
    assert b.property("kind") == "primary"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_theme.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.ui.theme'`.

- [ ] **Step 4: Implement `lumen/ui/theme.py`**

```python
"""Design tokens from .claude/lumenFrontEndUIReference/design/tokens.md, as code.
Accent is themeable: build_qss(accent) threads one color through everything."""

BG_APP = "#0d0e14"; BG_WINDOW = "#1a1b26"; BG_CHROME = "#16161e"
BG_OVERLAY = "#101018"; BG_PANEL = "#171821"; BG_PANEL_ALT = "#14151d"
BG_FIELD = "#0f1017"; BG_INSET = "#12131b"; BG_WAYBAR = "#0c0d13"
BORDER_STRONG = "#2a2f45"; BORDER_MED = "#262838"; BORDER_SOFT = "#20222e"; BORDER_FAINT = "#1a1c26"
TEXT_PRIMARY = "#c0caf5"; TEXT_SECONDARY = "#a9b1d6"; TEXT_MUTED = "#787c99"
TEXT_DIM = "#565f89"; TEXT_FAINT = "#3b4261"
ACCENT = "#7aa2f7"
ACCENT_SOFT = "rgba(122,162,247,0.12)"; ACCENT_MID = "rgba(122,162,247,0.20)"
OK = "#9ece6a"; WARN = "#e0af68"; NOW = "#f7768e"; INFO = "#7dcfff"
BOOK = "#bb9af7"; BOOK_DIM = "#8f83ac"; TAG_NEW = "#ff9e64"
FONT_MONO = "JetBrains Mono"; FONT_SANS = "IBM Plex Sans"
TAG_COLORS = {"work": ACCENT, "personal": BOOK, "home": OK,
              "admin": WARN, "tinker": INFO, "new": TAG_NEW}


def _soft(accent: str) -> str:
    r, g, b = (int(accent[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},0.12)"


def build_qss(accent: str = ACCENT) -> str:
    ac_soft = _soft(accent)
    return f"""
* {{ font-family: '{FONT_MONO}'; font-size: 13px; color: {TEXT_PRIMARY}; }}
QWidget[role="window"] {{ background: {BG_WINDOW}; }}
QWidget[role="chrome"] {{ background: {BG_CHROME}; }}
QWidget[role="waybar"] {{ background: {BG_WAYBAR}; }}
QWidget[role="overlay"] {{ background: {BG_OVERLAY}; }}

QLabel[role="eyebrow"] {{ font-size: 10px; color: {TEXT_FAINT}; }}
QLabel[role="accent-eyebrow"] {{ font-size: 10px; color: {accent}; }}
QLabel[role="h2"] {{ font-size: 16px; font-weight: 600; }}
QLabel[role="sub"] {{ font-size: 11px; color: {TEXT_DIM}; }}
QLabel[role="secondary"] {{ color: {TEXT_SECONDARY}; }}
QLabel[role="muted"] {{ font-size: 12px; color: {TEXT_MUTED}; }}
QLabel[role="dim"] {{ font-size: 11px; color: {TEXT_DIM}; }}
QLabel[role="faint"] {{ font-size: 10px; color: {TEXT_FAINT}; }}
QLabel[role="sans"] {{ font-family: '{FONT_SANS}'; }}
QLabel[role="chip"] {{ font-size: 10px; border: 1px solid {BORDER_STRONG};
                       border-radius: 3px; padding: 1px 5px; }}
QLabel[role="kbd"] {{ font-size: 10px; color: {TEXT_FAINT}; border: 1px solid {BORDER_STRONG};
                      border-radius: 3px; padding: 1px 5px; }}
QLabel[role="error"] {{ color: {NOW}; font-size: 12px; }}
QLabel[role="status"] {{ color: {WARN}; font-size: 11px; }}

QFrame[role="panel"] {{ background: {BG_PANEL}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; }}
QFrame[role="panel-alt"] {{ background: {BG_PANEL_ALT}; border: 1px solid {BORDER_SOFT}; border-radius: 8px; }}
QFrame[role="inset"] {{ background: {BG_INSET}; border: 1px solid {BORDER_MED}; border-radius: 7px; }}
QFrame[role="hline"] {{ background: {BORDER_FAINT}; border: none; max-height: 1px; }}

QPushButton {{ font-size: 11.5px; }}
QPushButton[kind="primary"] {{ background: {accent}; color: {BG_APP}; font-weight: 600;
    border: 1px solid {accent}; border-radius: 6px; padding: 6px 14px; }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid {BORDER_STRONG};
    color: {TEXT_SECONDARY}; border-radius: 6px; padding: 6px 12px; }}
QPushButton[kind="link"] {{ background: transparent; border: 1px solid {BORDER_STRONG};
    color: {accent}; font-size: 11px; border-radius: 6px; padding: 7px; }}
QPushButton[kind="soft"] {{ background: {ac_soft}; border: 1px solid {accent};
    color: {TEXT_PRIMARY}; font-size: 11px; border-radius: 5px; padding: 5px 12px; }}
QPushButton[kind="tab"] {{ background: transparent; border: none; color: {TEXT_DIM};
    font-size: 12px; padding: 10px 14px; border-bottom: 2px solid transparent; }}
QPushButton[kind="tab"][active="true"] {{ color: {TEXT_PRIMARY}; border-bottom: 2px solid {accent}; }}
QPushButton[kind="tab"]:hover {{ color: {TEXT_SECONDARY}; }}

QLineEdit {{ background: transparent; border: none; color: {TEXT_PRIMARY}; font-size: 16px; }}
QLineEdit[kind="field"] {{ background: {BG_FIELD}; border: 1px solid {BORDER_STRONG};
    border-radius: 5px; padding: 7px 9px; font-size: 12.5px; }}
QTextEdit {{ background: transparent; border: none; font-family: '{FONT_SANS}'; font-size: 14px; }}
QCheckBox {{ spacing: 10px; font-size: 13px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {TEXT_FAINT}; border-radius: 3px; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
"""
```

- [ ] **Step 5: Implement `lumen/ui/widgets.py`**

```python
"""Tiny factories so screens stay declarative and hex-free."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton


def label(text: str, role: str, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", role)
    lab.setWordWrap(wrap)
    return lab


def chip(text: str, color: str) -> QLabel:
    lab = label(text, "chip")
    lab.setStyleSheet(f"color: {color};")  # chip color is data, not theme
    return lab


class Panel(QFrame):
    def __init__(self, role: str = "panel"):
        super().__init__()
        self.setProperty("role", role)


def button(text: str, kind: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("kind", kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_theme.py -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/ui/test_theme.py lumen/ui/theme.py lumen/ui/widgets.py
git commit -m "Add UI theme (tokens as QSS with themeable accent) and widget factories

This commit used 3 prompts."
```

---

### Task 9: Confirmation dialog

**Files:**
- Modify: `tests/ui/test_confirm_dialog.py` (replace stub comment)
- Modify: `lumen/ui/confirm_dialog.py` (replace stub comment)

**Interfaces:**
- Consumes: `theme`, `widgets` (Task 8).
- Produces: `class ConfirmDialog(QDialog)` — `__init__(self, title: str, intro: str, fields: list[tuple[str, str]], confirm_label: str, parent=None)`; classmethod `ask(...) -> bool` (runs modal, True on confirm). Esc cancels; Ctrl+Return confirms. Every later write action uses this.

- [ ] **Step 1: Write the failing tests**

`tests/ui/test_confirm_dialog.py`:

```python
from PyQt6.QtCore import Qt

from lumen.ui.confirm_dialog import ConfirmDialog


def make(qtbot):
    dlg = ConfirmDialog(
        "Send email", "Lumen will send this message from your connected Gmail account.",
        [("To", "priya.nair@company.com"), ("Subject", "Re: sync interval defaults")],
        "Send email",
    )
    qtbot.addWidget(dlg)
    return dlg


def test_renders_fields_and_warning(qtbot):
    dlg = make(qtbot)
    texts = [lab.text() for lab in dlg.findChildren(type(dlg.title_label))]
    assert "Send email" in texts
    assert any("WRITE ACTION" in t for t in texts)
    assert "priya.nair@company.com" in texts


def test_escape_rejects(qtbot):
    dlg = make(qtbot)
    dlg.show()
    qtbot.keyClick(dlg, Qt.Key.Key_Escape)
    assert dlg.result() == 0


def test_ctrl_return_accepts(qtbot):
    dlg = make(qtbot)
    dlg.show()
    qtbot.keyClick(dlg, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert dlg.result() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_confirm_dialog.py -v`
Expected: FAIL — `ImportError: cannot import name 'ConfirmDialog'`.

- [ ] **Step 3: Implement `lumen/ui/confirm_dialog.py`**

```python
"""The safety checkpoint. Shown ONLY before external writes (send email, create
event). Deliberately heavier than the rest of the UI: accent border + warn line."""

from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QVBoxLayout

from lumen.ui import theme
from lumen.ui.widgets import Panel, button, label


class ConfirmDialog(QDialog):
    def __init__(self, title: str, intro: str, fields: list[tuple[str, str]],
                 confirm_label: str, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setFixedWidth(480)
        self.setStyleSheet(
            f"QDialog {{ background: #1c1d28; border: 1px solid {theme.ACCENT}; border-radius: 11px; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        self.title_label = label(title, "h2")
        warn = label("⚠ WRITE ACTION · TOUCHES A CONNECTED ACCOUNT", "status")
        root.addWidget(self.title_label)
        root.addWidget(warn)
        root.addWidget(label(intro, "sans", wrap=True))

        box = Panel("inset")
        grid = QGridLayout(box)
        grid.setContentsMargins(12, 10, 12, 10)
        for row, (k, v) in enumerate(fields):
            key = label(k, "dim")
            grid.addWidget(key, row, 0)
            grid.addWidget(label(v, "secondary", wrap=True), row, 1)
        root.addWidget(box)

        actions = QHBoxLayout()
        cancel = button("Cancel  esc", "ghost")
        confirm = button(f"{confirm_label}  ⌘↵", "primary")
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept)
        actions.addWidget(cancel, 1)
        actions.addWidget(confirm, 2)
        root.addLayout(actions)

        QShortcut(QKeySequence("Ctrl+Return"), self, self.accept)

    @classmethod
    def ask(cls, title: str, intro: str, fields: list[tuple[str, str]],
            confirm_label: str, parent=None) -> bool:
        return cls(title, intro, fields, confirm_label, parent).exec() == QDialog.DialogCode.Accepted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_confirm_dialog.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/ui/test_confirm_dialog.py lumen/ui/confirm_dialog.py
git commit -m "Add confirmation dialog component with esc/ctrl-return keys

This commit used 3 prompts."
```

---

### Task 10: Window shell

**Files:**
- Create: `lumen/ui/shell.py`, `tests/ui/test_shell.py`

**Interfaces:**
- Consumes: `theme`, `widgets` (Task 8).
- Produces: `class MainWindow(QWidget)` — `__init__(self, screens: dict[str, QWidget])` with keys `launcher, dashboard, calendar, mail, todos, books, settings` (any missing key gets a blank placeholder); `set_view(name: str)`; `pyqtSignal sleep_requested`; number keys 1–6 switch tabs, gear opens settings. Tab order: Launcher, Dashboard, Calendar, Mail, Todos, Books.

- [ ] **Step 1: Write the failing tests**

`tests/ui/test_shell.py`:

```python
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from lumen.ui.shell import MainWindow


def make(qtbot):
    win = MainWindow({name: QLabel(name) for name in
                      ("launcher", "dashboard", "calendar", "mail", "todos", "books", "settings")})
    qtbot.addWidget(win)
    win.show()
    return win


def test_starts_on_launcher(qtbot):
    win = make(qtbot)
    assert win.current_view() == "launcher"


def test_number_keys_switch_tabs(qtbot):
    win = make(qtbot)
    qtbot.keyClick(win, Qt.Key.Key_3)
    assert win.current_view() == "calendar"
    qtbot.keyClick(win, Qt.Key.Key_6)
    assert win.current_view() == "books"


def test_gear_opens_settings(qtbot):
    win = make(qtbot)
    win.gear.click()
    assert win.current_view() == "settings"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_shell.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.ui.shell'`.

- [ ] **Step 3: Implement `lumen/ui/shell.py`**

```python
"""Window shell: title bar / tab bar / stacked views, per tokens.md layout.
Holds zero business logic — it routes clicks and keystrokes to screens."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import button, label

VIEWS = ["launcher", "dashboard", "calendar", "mail", "todos", "books"]


class MainWindow(QWidget):
    sleep_requested = pyqtSignal()

    def __init__(self, screens: dict[str, QWidget]):
        super().__init__()
        self.setWindowTitle("lumen")
        self.setProperty("role", "window")
        self.resize(1320, 800)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # title bar
        titlebar = QWidget()
        titlebar.setProperty("role", "chrome")
        titlebar.setFixedHeight(38)
        tb = QHBoxLayout(titlebar)
        tb.setContentsMargins(14, 0, 14, 0)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {theme.OK}; font-size: 9px;")
        tb.addWidget(dot)
        tb.addWidget(label("lumen", "secondary"))
        tb.addWidget(label("daily assistant", "dim"))
        tb.addStretch()
        tb.addWidget(label("local · qwen3:4b", "dim"))
        self.sleep_btn = button("idle 10m — sleep", "ghost")
        self.sleep_btn.clicked.connect(self.sleep_requested.emit)
        tb.addWidget(self.sleep_btn)
        root.addWidget(titlebar)

        # tab bar
        tabbar = QWidget()
        tabbar.setProperty("role", "chrome")
        tabbar.setFixedHeight(38)
        tabs = QHBoxLayout(tabbar)
        tabs.setContentsMargins(8, 0, 8, 0)
        tabs.setSpacing(2)
        self._tab_buttons: dict[str, QWidget] = {}
        for i, name in enumerate(VIEWS, start=1):
            b = button(f"{name.capitalize()}  {i}", "tab")
            b.clicked.connect(lambda _, n=name: self.set_view(n))
            self._tab_buttons[name] = b
            tabs.addWidget(b)
        tabs.addStretch()
        self.gear = button("⚙", "tab")
        self.gear.clicked.connect(lambda: self.set_view("settings"))
        tabs.addWidget(self.gear)
        root.addWidget(tabbar)

        # stacked views
        self._stack = QStackedWidget()
        self._names: list[str] = []
        for name in VIEWS + ["settings"]:
            self._stack.addWidget(screens.get(name) or QLabel(f"{name} — coming soon"))
            self._names.append(name)
        root.addWidget(self._stack, 1)

        for i, name in enumerate(VIEWS, start=1):
            QShortcut(QKeySequence(str(i)), self, lambda n=name: self.set_view(n))

        self.set_view("launcher")

    def set_view(self, name: str) -> None:
        self._stack.setCurrentIndex(self._names.index(name))
        for tab_name, b in self._tab_buttons.items():
            b.setProperty("active", "true" if tab_name == name else "false")
            b.style().unpolish(b)
            b.style().polish(b)

    def current_view(self) -> str:
        return self._names[self._stack.currentIndex()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_shell.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/ui/test_shell.py lumen/ui/shell.py
git commit -m "Add window shell: chrome, tab bar, stacked views, number-key nav

This commit used 3 prompts."
```

---

### Task 11: Dashboard screen (static)

**Files:**
- Modify: `lumen/ui/dashboard.py` (replace stub comment)
- Create: `tests/ui/test_screens.py` (grows one test per screen task)

**Interfaces:**
- Consumes: `theme`, `widgets`.
- Produces: `class DashboardScreen(QWidget)` — no-arg constructor, static content. Reference: `design/dashboard.html` (3-column grid `290 | 1fr | 320`).

- [ ] **Step 1: Write the failing test** — create `tests/ui/test_screens.py`:

```python
from PyQt6.QtWidgets import QLabel

from lumen.ui.dashboard import DashboardScreen


def texts(widget) -> str:
    return " | ".join(lab.text() for lab in widget.findChildren(QLabel))


def test_dashboard_has_three_columns(qtbot):
    w = DashboardScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "TODAY · TODOS" in t and "TODAY · CALENDAR" in t and "UNREAD · MAIL" in t
    assert "Call the dentist" in t
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_screens.py -v`
Expected: FAIL — `ImportError: cannot import name 'DashboardScreen'`.

- [ ] **Step 3: Implement `lumen/ui/dashboard.py`**

```python
"""Dashboard: today at a glance. Static skeleton — real data lands in Phases 2/5/6."""

from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import Panel, button, chip, label

TODOS = [("Call the dentist", "personal", False),
         ("Reply to Priya re: sync defaults", "work", False),
         ("Water the plants", "home", True)]
EVENTS = [("09:30", "Standup — Platform", 75, 22, False),
          ("11:00", "1:1 with Priya", 150, 22, False),
          ("13:00", "Lunch", 250, 47, False),
          ("15:30", "Design review: Lumen", 375, 34, True),
          ("18:00", "Gym", 500, 47, False)]
MAIL = [("GitHub", "08:12", "PR #142: swap to local model runtime"),
        ("Priya Nair", "07:40", "Re: sync interval defaults"),
        ("Dr. Okafor's office", "Tue", "Appointment reminder — Jul 9"),
        ("Sarah Chen", "Mon", "Book club: next pick?")]


def _col_head(eye: str, n: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 9)
    h.addWidget(label(eye, "accent-eyebrow"))
    h.addStretch()
    h.addWidget(label(n, "dim"))
    return w


class DashboardScreen(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)

        head = QHBoxLayout()
        head.addWidget(label("Mon · Jul 6", "h2"))
        head.addWidget(label("today at a glance", "sub"))
        head.addStretch()
        head.addWidget(label("● synced 2m ago", "faint"))
        root.addLayout(head)

        grid = QGridLayout()
        grid.setSpacing(22)
        grid.setColumnMinimumWidth(0, 290)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, 320)

        # 1 · todos
        todos = QVBoxLayout()
        todos.addWidget(_col_head("TODAY · TODOS", "5 open"))
        for text, tag, done in TODOS:
            row = QHBoxLayout()
            box = QLabel("✓" if done else "")
            box.setFixedSize(15, 15)
            box.setStyleSheet(
                f"background: {theme.ACCENT if done else 'transparent'};"
                f"border: 1px solid {theme.ACCENT if done else theme.TEXT_FAINT}; border-radius: 3px;"
                f"color: {theme.BG_WINDOW}; font-size: 10px;")
            item = label(text, "secondary" if not done else "dim")
            row.addWidget(box)
            row.addWidget(item, 1)
            row.addWidget(chip(tag, theme.TAG_COLORS[tag]))
            todos.addLayout(row)
        todos.addWidget(button("Manage todos →", "link"))
        todos.addStretch()

        # 2 · day calendar: fixed-height panel, absolutely positioned blocks
        cal = QVBoxLayout()
        cal.addWidget(_col_head("TODAY · CALENDAR", "5 events"))
        canvas = Panel("panel-alt")
        canvas.setFixedHeight(620)
        for hour in range(8, 21):                       # 08:00–20:00, 50px/hr
            y = (hour - 8) * 50 + 6
            lab = label(f"{hour:02d}:00", "faint")
            lab.setParent(canvas)
            lab.move(10, y)
            line = QFrame(canvas)
            line.setStyleSheet(f"background: {theme.BORDER_FAINT};")
            line.setGeometry(52, y + 7, 10_000, 1)
        for time, title, top, height, is_next in EVENTS:
            block = QFrame(canvas)
            block.setStyleSheet(
                f"background: {theme.ACCENT_MID if is_next else theme.ACCENT_SOFT};"
                f"border-left: 2px solid {theme.ACCENT}; border-radius: 5px;")
            block.setGeometry(58, top + 6, 480, height)
            v = QVBoxLayout(block)
            v.setContentsMargins(9, 2, 9, 2)
            v.setSpacing(0)
            v.addWidget(label(title, "secondary"))
            v.addWidget(label(f"{time}", "faint"))
        now = QFrame(canvas)
        now.setStyleSheet(f"background: {theme.NOW};")
        now.setGeometry(52, 106, 10_000, 1)
        cal.addWidget(canvas)

        # 3 · unread mail
        mail = QVBoxLayout()
        mail.addWidget(_col_head("UNREAD · MAIL", "4 unread"))
        for sender, when, subj in MAIL:
            top_row = QHBoxLayout()
            top_row.addWidget(label(f"● {sender}", "secondary"))
            top_row.addStretch()
            top_row.addWidget(label(when, "faint"))
            mail.addLayout(top_row)
            mail.addWidget(label(subj, "muted"))
        mail.addWidget(button("Open mail →", "link"))
        mail.addStretch()

        for col, layout in enumerate((todos, cal, mail)):
            holder = QWidget()
            holder.setLayout(layout)
            grid.addWidget(holder, 0, col)
        root.addLayout(grid, 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ui/test_screens.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/ui/test_screens.py lumen/ui/dashboard.py
git commit -m "Add dashboard screen skeleton: todos, day-calendar blocks, unread mail

This commit used 3 prompts."
```

---

### Task 12: Calendar screen (static, Month view)

**Files:**
- Modify: `lumen/ui/calendar_view.py` (replace stub comment)
- Modify: `tests/ui/test_screens.py` (append test)

**Interfaces:**
- Consumes: `theme`, `widgets`.
- Produces: `class CalendarScreen(QWidget)` — toolbar (‹ Today ›, month title, legend, Month/Week/Day segment, + Event) and a 7×6 month grid. Week/Day buttons present but inert this phase. Reference: `design/calendar.html`.

- [ ] **Step 1: Append the failing test** to `tests/ui/test_screens.py`:

```python
def test_calendar_month_grid(qtbot):
    from lumen.ui.calendar_view import CalendarScreen
    w = CalendarScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "July 2026" in t and "MON" in t and "SUN" in t
    assert "Standup — Platf…" in t
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_screens.py -v -k calendar`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement `lumen/ui/calendar_view.py`**

```python
"""Calendar: Month view skeleton. Week/Day + real events arrive in Phase 5."""

from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import button, chip, label

CATEGORY = {"work": theme.ACCENT, "personal": theme.BOOK,
            "health": theme.OK, "social": theme.WARN}
# (day-number, out-of-month, is-today, [(title, category), ...])
WEEKS = [
    [(29, True, False, []), (30, True, False, []), (1, False, False, []), (2, False, False, []),
     (3, False, False, []), (4, False, False, []),
     (5, False, False, [("Long run", "health"), ("Sunday roast @ …", "social")])],
    [(6, False, True, [("Standup — Platf…", "work"), ("1:1 with Priya", "work"), ("Lunch", "personal")]),
     (7, False, False, [("Standup — Platf…", "work"), ("Sprint planning", "work"), ("Book club: The …", "social")]),
     (8, False, False, [("Standup — Platf…", "work"), ("Deep work — syn…", "work"), ("Lunch w/ Sam", "social")]),
     (9, False, False, [("Standup — Platf…", "work"), ("Dental cleaning", "health"), ("Release review", "work")]),
     (10, False, False, [("Standup — Platf…", "work"), ("Sprint retro", "work"), ("Gym", "health")]),
     (11, False, False, [("Hiking — Ridge …", "social")]), (12, False, False, [])],
    [(13, False, False, []), (14, False, False, [("Quarterly plann…", "work")]), (15, False, False, []),
     (16, False, False, []), (17, False, False, []), (18, False, False, []), (19, False, False, [])],
    [(20, False, False, [("PTO", "personal")]), (21, False, False, []), (22, False, False, []),
     (23, False, False, []), (24, False, False, [("Conf talk: loca…", "work")]),
     (25, False, False, []), (26, False, False, [])],
]


class CalendarScreen(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(22, 14, 22, 14)
        for text in ("‹", "Today", "›"):
            bar.addWidget(button(text, "ghost"))
        bar.addWidget(label("July 2026", "h2"))
        bar.addStretch()
        for cat, color in CATEGORY.items():
            bar.addWidget(chip(cat, color))
        for i, seg in enumerate(("Month", "Week", "Day")):
            b = button(seg, "soft" if i == 0 else "ghost")
            bar.addWidget(b)
        bar.addWidget(button("+ Event", "primary"))
        root.addLayout(bar)

        wd = QHBoxLayout()
        wd.setContentsMargins(0, 0, 0, 0)
        for d in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"):
            wd.addWidget(label(d, "eyebrow"), 1)
        root.addLayout(wd)

        grid = QGridLayout()
        grid.setSpacing(0)
        for r, week in enumerate(WEEKS):
            for c, (num, out, today, events) in enumerate(week):
                cell = QFrame()
                cell.setStyleSheet(
                    f"border: 1px solid {theme.BORDER_FAINT};"
                    + (f"background: {theme.ACCENT_SOFT};" if today
                       else f"background: {theme.BG_PANEL_ALT};" if out else ""))
                v = QVBoxLayout(cell)
                v.setContentsMargins(6, 5, 6, 4)
                v.setSpacing(2)
                v.addWidget(label(str(num), "faint" if out else "secondary"))
                for title, cat in events[:3]:
                    v.addWidget(chip(title, CATEGORY[cat]))
                if len(events) > 3:
                    v.addWidget(label(f"+{len(events) - 3} more", "faint"))
                v.addStretch()
                cell.setMinimumHeight(104)
                grid.addWidget(cell, r, c)
        root.addLayout(grid, 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ui/test_screens.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/ui/test_screens.py lumen/ui/calendar_view.py
git commit -m "Add calendar screen skeleton: toolbar and month grid with category chips

This commit used 3 prompts."
```

---

### Task 13: Mail screen (static, exercises ConfirmDialog)

**Files:**
- Modify: `lumen/ui/mail.py` (replace stub comment)
- Modify: `tests/ui/test_screens.py` (append tests)

**Interfaces:**
- Consumes: `theme`, `widgets`, `ConfirmDialog.ask` (Task 9).
- Produces: `class MailScreen(QWidget)` — two-pane split (334 | 1fr): message list + reading pane; the Reply button calls `ConfirmDialog.ask(...)` (the placeholder trigger the spec requires). Reference: `design/mail.html`.

- [ ] **Step 1: Append the failing tests** to `tests/ui/test_screens.py`:

```python
def test_mail_two_pane(qtbot):
    from lumen.ui.mail import MailScreen
    w = MailScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "Inbox" in t and "Re: sync interval defaults" in t and "Priya Nair" in t


def test_mail_reply_opens_confirm(qtbot, monkeypatch):
    from lumen.ui import mail as mail_mod
    calls = {}
    monkeypatch.setattr(mail_mod.ConfirmDialog, "ask",
                        classmethod(lambda cls, *a, **k: calls.setdefault("asked", True)))
    w = mail_mod.MailScreen()
    qtbot.addWidget(w)
    w.reply_btn.click()
    assert calls.get("asked")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_screens.py -v -k mail`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement `lumen/ui/mail.py`**

```python
"""Mail: two-pane inbox skeleton. Real sync is Phase 6; Reply demonstrates the
write-confirmation flow with placeholder content."""

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.confirm_dialog import ConfirmDialog
from lumen.ui.widgets import button, label

MESSAGES = [
    ("GitHub", "08:12", "PR #142: swap to local model runtime", False),
    ("Priya Nair", "07:40", "Re: sync interval defaults", True),
    ("Dr. Okafor's office", "Tue", "Appointment reminder — Jul 9", False),
    ("Sarah Chen", "Mon", "Book club: next pick?", False),
    ("Linux Weekly", "Wed", "Wayland color management lands", False),
]
BODY = """Thanks for writing this up.

15 minutes as the default feels right — long enough to stay quiet, short enough \
that the tray never goes stale. Can we expose it under [sync] so power users can \
drop it to 5?

— Priya"""


class MailScreen(QWidget):
    def __init__(self):
        super().__init__()
        split = QHBoxLayout(self)
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)

        # message list
        lst = QVBoxLayout()
        lst.setContentsMargins(16, 16, 16, 16)
        head = QHBoxLayout()
        head.addWidget(label("Inbox", "h2"))
        head.addWidget(label("4 unread", "dim"))
        head.addStretch()
        lst.addLayout(head)
        for sender, when, subj, selected in MESSAGES:
            row = QFrame()
            if selected:
                row.setStyleSheet(
                    f"background: {theme.ACCENT_SOFT}; border-left: 2px solid {theme.ACCENT};")
            rv = QVBoxLayout(row)
            rv.setContentsMargins(10, 8, 10, 8)
            rv.setSpacing(1)
            top = QHBoxLayout()
            top.addWidget(label(sender, "secondary"))
            top.addStretch()
            top.addWidget(label(when, "faint"))
            rv.addLayout(top)
            rv.addWidget(label(subj, "muted"))
            lst.addWidget(row)
        lst.addStretch()
        holder = QWidget()
        holder.setLayout(lst)
        holder.setFixedWidth(334)
        split.addWidget(holder)

        # reading pane
        pane = QVBoxLayout()
        pane.setContentsMargins(26, 20, 26, 20)
        pane.addWidget(label("Re: sync interval defaults", "h2"))
        meta = QHBoxLayout()
        meta.addWidget(label("Priya Nair", "secondary"))
        meta.addWidget(label("to me · Thu · 07:40", "dim"))
        meta.addStretch()
        self.reply_btn = button("↳ Reply", "primary")
        self.reply_btn.clicked.connect(self._reply)
        meta.addWidget(self.reply_btn)
        meta.addWidget(button("Archive", "ghost"))
        pane.addLayout(meta)
        pane.addWidget(label(BODY, "sans", wrap=True))
        pane.addStretch()
        pane_holder = QWidget()
        pane_holder.setLayout(pane)
        split.addWidget(pane_holder, 1)

    def _reply(self) -> None:
        ConfirmDialog.ask(
            "Send email",
            "Lumen will send this message from your connected Gmail account.",
            [("To", "priya.nair@company.com"),
             ("Subject", "Re: sync interval defaults"),
             ("Body", "(placeholder — email send lands in Phase 7)")],
            "Send email", parent=self)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_screens.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/ui/test_screens.py lumen/ui/mail.py
git commit -m "Add mail screen skeleton: two-pane inbox, reply wired to confirm dialog

This commit used 3 prompts."
```

---

### Task 14: Todo screen (static)

**Files:**
- Modify: `lumen/ui/todo_manager.py` (replace stub comment)
- Modify: `tests/ui/test_screens.py` (append test)

**Interfaces:**
- Consumes: `theme`, `widgets`.
- Produces: `class TodoScreen(QWidget)` — centered 820px column: header, add-row, TODAY / UPCOMING / NO DATE groups. Reference: `design/todo-manager.html`.

- [ ] **Step 1: Append the failing test** to `tests/ui/test_screens.py`:

```python
def test_todos_groups(qtbot):
    from lumen.ui.todo_manager import TodoScreen
    w = TodoScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "TODAY" in t and "UPCOMING" in t and "NO DATE" in t
    assert "Renew lumen.sh domain" in t
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_screens.py -v -k todos`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement `lumen/ui/todo_manager.py`**

```python
"""Todos: direct-manipulation skeleton. CRUD + SQLite land in Phase 2."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import Panel, button, chip, label

GROUPS = [
    ("TODAY", theme.ACCENT, [
        ("Call the dentist", None, "personal", False),
        ("Reply to Priya re: sync defaults", None, "work", False),
        ("Water the plants", None, "home", True)]),
    ("UPCOMING", theme.WARN, [
        ("Draft Lumen README", "Sat", "work", False),
        ("Renew lumen.sh domain", "Jul 9", "admin", False)]),
    ("NO DATE", theme.TEXT_DIM, [
        ("Read “Systemantics”", None, "personal", False),
        ("Try river WM on the laptop", None, "tinker", False)]),
]


class TodoScreen(QWidget):
    def __init__(self):
        super().__init__()
        outer = QHBoxLayout(self)
        col = QWidget()
        col.setMaximumWidth(820)
        outer.addWidget(col, alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        root = QVBoxLayout(col)
        root.setContentsMargins(26, 22, 26, 22)

        head = QHBoxLayout()
        head.addWidget(label("Todos", "h2"))
        head.addWidget(label("5 open · edit directly, no assistant needed", "sub"))
        head.addStretch()
        root.addLayout(head)

        add = Panel()
        ah = QHBoxLayout(add)
        ah.setContentsMargins(13, 11, 13, 11)
        plus = label("+", "accent-eyebrow")
        field = QLineEdit()
        field.setPlaceholderText("Add a todo… (⏎ to save)")
        ah.addWidget(plus)
        ah.addWidget(field, 1)
        ah.addWidget(button("Add", "soft"))
        root.addWidget(add)

        for group, color, items in GROUPS:
            g = label(group, "eyebrow")
            g.setStyleSheet(f"color: {color};")
            root.addWidget(g)
            for text, due, tag, done in items:
                row = QHBoxLayout()
                box = label("✓" if done else "", "chip")
                box.setFixedSize(15, 15)
                box.setStyleSheet(
                    f"background: {theme.ACCENT if done else 'transparent'};"
                    f"border: 1px solid {theme.ACCENT if done else theme.TEXT_FAINT};"
                    f"color: {theme.BG_WINDOW};")
                row.addWidget(box)
                row.addWidget(label(text, "dim" if done else "secondary"), 1)
                if due:
                    row.addWidget(chip(due, theme.WARN))
                row.addWidget(chip(tag, theme.TAG_COLORS[tag]))
                row.addWidget(label("✕", "faint"))
                root.addLayout(row)
        root.addStretch()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ui/test_screens.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/ui/test_screens.py lumen/ui/todo_manager.py
git commit -m "Add todo screen skeleton: add row and date-grouped items

This commit used 3 prompts."
```

---

### Task 15: Books screen (static)

**Files:**
- Modify: `lumen/ui/book_catalog.py` (replace stub comment)
- Modify: `tests/ui/test_screens.py` (append test)

**Interfaces:**
- Consumes: `theme`, `widgets`.
- Produces: `class BooksScreen(QWidget)` — reading log (add form + 4 entries) left, dashed-purple recommendations panel (3 recs) right. Reference: `design/book-catalog.html`.

- [ ] **Step 1: Append the failing test** to `tests/ui/test_screens.py`:

```python
def test_books_log_and_recs(qtbot):
    from lumen.ui.book_catalog import BooksScreen
    w = BooksScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "Reading log" in t and "The Left Hand of Darkness" in t
    assert "SUGGESTED — NOT YET READ" in t and "Solaris" in t
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_screens.py -v -k books`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement `lumen/ui/book_catalog.py`**

```python
"""Books: reading log + LLM recommendations skeleton. Real catalog is Phase 4.
Recs panel is deliberately visually distinct (dashed purple) from the log."""

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import Panel, button, label

BOOKS = [
    ("The Left Hand of Darkness", "Ursula K. Le Guin", "★★★★★", "finished Jun 28",
     "Slow burn, worth it. Gethen worldbuilding stuck with me."),
    ("Project Hail Mary", "Andy Weir", "★★★★☆", "finished Jun 10",
     "Propulsive. Rocky carries it more than the science."),
    ("The Dispossessed", "Ursula K. Le Guin", "★★★★★", "finished May 22",
     "Ambiguous utopia. Reread candidate."),
    ("Piranesi", "Susanna Clarke", "★★★★☆", "finished Apr 30",
     "Quiet, strange, lingers for weeks."),
]
RECS = [
    ("A Fire Upon the Deep", "Vernor Vinge",
     "↳ Big-idea space opera near Project Hail Mary's energy, but denser."),
    ("The Word for World Is Forest", "Ursula K. Le Guin",
     "↳ You rated two Le Guin novels 5★ — the short Hainish entry you haven't logged."),
    ("Solaris", "Stanisław Lem",
     "↳ Shares Piranesi's uncanny, unknowable-space mood."),
]


class BooksScreen(QWidget):
    def __init__(self):
        super().__init__()
        split = QHBoxLayout(self)
        split.setContentsMargins(26, 22, 26, 22)
        split.setSpacing(24)

        # reading log
        log = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(label("Reading log", "h2"))
        head.addWidget(label("4 books · your catalog", "sub"))
        head.addStretch()
        log.addLayout(head)

        form = Panel()
        fv = QVBoxLayout(form)
        fv.setContentsMargins(12, 12, 12, 12)
        fv.addWidget(label("+ ADD ENTRY", "eyebrow"))
        line1 = QHBoxLayout()
        for placeholder, stretch in (("Title", 2), ("Author", 1)):
            f = QLineEdit()
            f.setProperty("kind", "field")
            f.setPlaceholderText(placeholder)
            line1.addWidget(f, stretch)
        line1.addWidget(label("★5", "status"))
        fv.addLayout(line1)
        line2 = QHBoxLayout()
        notes = QLineEdit()
        notes.setProperty("kind", "field")
        notes.setPlaceholderText("Notes (free text)")
        line2.addWidget(notes, 1)
        line2.addWidget(button("Log", "soft"))
        fv.addLayout(line2)
        log.addWidget(form)

        for title, author, stars, fin, note in BOOKS:
            row1 = QHBoxLayout()
            row1.addWidget(label(title, "secondary"))
            row1.addWidget(label(author, "muted"))
            row1.addStretch()
            stars_lab = label(stars, "status")
            row1.addWidget(stars_lab)
            log.addLayout(row1)
            log.addWidget(label(fin, "faint"))
            log.addWidget(label(note, "sans", wrap=True))
        log.addStretch()
        log_holder = QWidget()
        log_holder.setLayout(log)
        log_holder.setMinimumWidth(420)
        split.addWidget(log_holder, 1)

        # recommendations
        recs = QVBoxLayout()
        rhead = QHBoxLayout()
        title_lab = label("Suggested next", "h2")
        title_lab.setStyleSheet(f"color: {theme.BOOK};")
        rhead.addWidget(title_lab)
        rhead.addWidget(label("3 · from the LLM", "sub"))
        rhead.addStretch()
        recs.addLayout(rhead)

        box = QFrame()
        box.setStyleSheet(
            f"background: #16141f; border: 1px dashed #3b3155; border-radius: 9px;")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(14, 9, 14, 12)
        eye = label("◆ SUGGESTED — NOT YET READ", "eyebrow")
        eye.setStyleSheet("color: #6b5d8f;")
        bv.addWidget(eye)
        for title, author, why in RECS:
            row1 = QHBoxLayout()
            t_lab = label(title, "secondary")
            t_lab.setStyleSheet("color: #c9b8f0;")
            row1.addWidget(t_lab)
            a_lab = label(author, "faint")
            row1.addWidget(a_lab)
            row1.addStretch()
            bv.addLayout(row1)
            why_lab = label(why, "sans", wrap=True)
            why_lab.setStyleSheet(f"color: {theme.BOOK_DIM}; font-size: 12px;")
            bv.addWidget(why_lab)
            add = button("+ add to log", "ghost")
            bv.addWidget(add)
        bv.addWidget(label("refreshed from your catalog · Le Guin + hard-SF signal", "faint"))
        recs.addWidget(box)
        recs.addStretch()
        recs_holder = QWidget()
        recs_holder.setLayout(recs)
        recs_holder.setFixedWidth(360)
        split.addWidget(recs_holder)
```

Note: the recs panel's dashed-purple frame colors (`#16141f`, `#3b3155`, `#c9b8f0`, `#6b5d8f`) are unique to this panel in the mockups and not in tokens.md's tables — keeping them local to this file with this comment is acceptable; do not promote them to theme.py.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ui/test_screens.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/ui/test_screens.py lumen/ui/book_catalog.py
git commit -m "Add books screen skeleton: reading log and dashed recommendations panel

This commit used 3 prompts."
```

---

### Task 16: Settings screen (static)

**Files:**
- Modify: `lumen/ui/settings.py` (replace stub comment)
- Modify: `tests/ui/test_screens.py` (append test)

**Interfaces:**
- Consumes: `theme`, `widgets`.
- Produces: `class SettingsScreen(QWidget)` — centered 760px config-file-styled view: `[accounts]`, `[mcp_servers]` toggle rows; `[model]` / `[sync]` key=value columns showing Lumen's real defaults (`qwen3:4b`, idle 10m, sync 5m). Reference: `design/settings.html`.

- [ ] **Step 1: Append the failing test** to `tests/ui/test_screens.py`:

```python
def test_settings_sections(qtbot):
    from lumen.ui.settings import SettingsScreen
    w = SettingsScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "[accounts]" in t and "[mcp_servers]" in t and "[model]" in t and "[sync]" in t
    assert "qwen3:4b" in t
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_screens.py -v -k settings`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement `lumen/ui/settings.py`**

```python
"""Settings: a config file rendered nicely. Flat, no sidebar. Static this phase;
live toggles/hot-reload arrive with the features they control."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import label

ACCOUNTS = [("gmail", "not connected · Phase 6", False),
            ("google_calendar", "not connected · Phase 5", False)]
SERVERS = [("search", "brave-search · Phase 3", False),
           ("books_lookup", "openlibrary · Phase 4", False)]
MODEL_KV = [("runtime", '"ollama"'), ("name", '"qwen3:4b"'),
            ("idle_unload_minutes", "10  # unload after 10m")]
SYNC_KV = [("interval", "5  # minutes"), ("confirm_writes", "true  # email/calendar")]


def _kv_block(header: str, rows: list[tuple[str, str]]) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.addWidget(label(header, "accent-eyebrow"))
    for k, val in rows:
        v.addWidget(label(f"{k} = {val}", "muted"))
    return w


class SettingsScreen(QWidget):
    def __init__(self):
        super().__init__()
        outer = QHBoxLayout(self)
        col = QWidget()
        col.setMaximumWidth(760)
        outer.addWidget(col, alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        root = QVBoxLayout(col)
        root.setContentsMargins(26, 22, 26, 22)

        head = QHBoxLayout()
        head.addWidget(label("Settings", "h2"))
        head.addWidget(label("lumen/config.toml", "sub"))
        head.addStretch()
        root.addLayout(head)
        root.addWidget(label("# edited here or in your editor", "faint"))

        for section, rows in (("[accounts]", ACCOUNTS), ("[mcp_servers]", SERVERS)):
            root.addWidget(label(section, "accent-eyebrow"))
            for key, desc, on in rows:
                row = QHBoxLayout()
                row.addWidget(label(key, "secondary"))
                row.addWidget(label(desc, "dim"), 1)
                sw = QCheckBox()
                sw.setChecked(on)
                sw.setEnabled(False)  # cosmetic until the feature phase wires it
                row.addWidget(sw)
                root.addLayout(row)

        cols = QGridLayout()
        cols.addWidget(_kv_block("[model]", MODEL_KV), 0, 0)
        cols.addWidget(_kv_block("[sync]", SYNC_KV), 0, 1)
        root.addLayout(cols)
        root.addStretch()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ui/test_screens.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/ui/test_screens.py lumen/ui/settings.py
git commit -m "Add settings screen skeleton: config-file-styled sections with real defaults

This commit used 3 prompts."
```

---

### Task 17: UI daemon client

**Files:**
- Create: `lumen/ui/daemon_client.py`, `tests/ui/test_daemon_client.py`

**Interfaces:**
- Consumes: the daemon wire protocol (Task 5): send `{"id", "type", "payload"}` + `\n`; receive `{"id", "chunk"|"done"|"error"}` lines.
- Produces: `class DaemonClient(QObject)` — signals `chunk(str)`, `done()`, `error(str)`; methods `send(type_: str, payload: dict) -> None`, `sleep_model() -> None`. Connects lazily; queues writes until connected; emits `error` with a "daemon offline" hint if the socket can't connect. Constructor: `DaemonClient(socket_path: str, parent=None)`.

- [ ] **Step 1: Write the failing tests**

`tests/ui/test_daemon_client.py`:

```python
import json

from PyQt6.QtNetwork import QLocalServer

from lumen.ui.daemon_client import DaemonClient


def test_send_and_stream(qtbot, tmp_path):
    path = str(tmp_path / "d.sock")
    server = QLocalServer()
    assert server.listen(path)

    received = []

    def on_new_conn():
        conn = server.nextPendingConnection()

        def on_ready():
            req = json.loads(bytes(conn.readLine()))
            received.append(req)
            for resp in ({"id": req["id"], "chunk": "hi"}, {"id": req["id"], "done": True}):
                conn.write(json.dumps(resp).encode() + b"\n")
            conn.flush()

        conn.readyRead.connect(on_ready)

    server.newConnection.connect(on_new_conn)

    client = DaemonClient(path)
    chunks = []
    client.chunk.connect(chunks.append)
    with qtbot.waitSignal(client.done, timeout=2000):
        client.send("chat", {"message": "hello"})
    assert chunks == ["hi"]
    assert received[0]["type"] == "chat"
    assert received[0]["payload"] == {"message": "hello"}
    server.close()


def test_offline_emits_error(qtbot, tmp_path):
    client = DaemonClient(str(tmp_path / "missing.sock"))
    with qtbot.waitSignal(client.error, timeout=2000) as blocker:
        client.send("chat", {"message": "hello"})
    assert "daemon offline" in blocker.args[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_daemon_client.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `lumen/ui/daemon_client.py`**

```python
"""Qt-side IPC client. QLocalSocket speaks AF_UNIX when the name is a path.
No business logic here — serialize, deserialize, emit signals."""

import itertools
import json

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalSocket

OFFLINE_MSG = ("daemon offline — start it with: uv run lumen-daemon "
               "(or systemctl --user start lumen-daemon)")


class DaemonClient(QObject):
    chunk = pyqtSignal(str)
    done = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, socket_path: str, parent=None):
        super().__init__(parent)
        self._path = socket_path
        self._ids = itertools.count(1)
        self._buf = b""
        self._pending: list[bytes] = []
        self._sock = QLocalSocket(self)
        self._sock.readyRead.connect(self._on_ready_read)
        self._sock.connected.connect(self._flush_pending)
        self._sock.errorOccurred.connect(lambda _e: self.error.emit(OFFLINE_MSG))

    def send(self, type_: str, payload: dict) -> None:
        line = json.dumps({"id": next(self._ids), "type": type_, "payload": payload}).encode() + b"\n"
        if self._sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self._sock.write(line)
        else:
            self._pending.append(line)
            if self._sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                self._sock.connectToServer(self._path)

    def sleep_model(self) -> None:
        self.send("sleep", {})

    def _flush_pending(self) -> None:
        for line in self._pending:
            self._sock.write(line)
        self._pending.clear()

    def _on_ready_read(self) -> None:
        self._buf += bytes(self._sock.readAll())
        while b"\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n", 1)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "error" in msg:
                self.error.emit(msg["error"])
            elif msg.get("done"):
                self.done.emit()
            elif "chunk" in msg:
                self.chunk.emit(msg["chunk"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_daemon_client.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/ui/test_daemon_client.py lumen/ui/daemon_client.py
git commit -m "Add Qt daemon client: lazy-connect QLocalSocket with streaming signals

This commit used 3 prompts."
```

---

### Task 18: Launcher (live) + overlay

**Files:**
- Modify: `lumen/ui/launcher.py` (replace stub comment)
- Create: `tests/ui/test_launcher.py`

**Interfaces:**
- Consumes: an object with `DaemonClient`'s interface (Task 17) — injected, so tests use a fake.
- Produces:
  - `class LauncherScreen(QWidget)` — `__init__(self, client)`; input row + hints block + status label + streaming response area + keybind footer. States: idle (hints visible), waking (status shows "waking model…" if no chunk within 1500 ms), streaming (response grows), error (status shows the error). Enter submits `client.send("chat", {"message": text})`.
  - `class LauncherOverlay(QDialog)` — frameless 620px overlay wrapping a `LauncherScreen`; `toggle()` shows/hides; Esc closes. Reference: `design/quick-launcher.html`.

- [ ] **Step 1: Write the failing tests**

`tests/ui/test_launcher.py`:

```python
from PyQt6.QtCore import QObject, Qt, pyqtSignal

from lumen.ui.launcher import LauncherScreen


class FakeClient(QObject):
    chunk = pyqtSignal(str)
    done = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, type_, payload):
        self.sent.append((type_, payload))


def make(qtbot):
    client = FakeClient()
    w = LauncherScreen(client)
    qtbot.addWidget(w)
    w.show()
    return w, client


def test_enter_sends_chat(qtbot):
    w, client = make(qtbot)
    w.input.setText("what is a monad")
    qtbot.keyClick(w.input, Qt.Key.Key_Return)
    assert client.sent == [("chat", {"message": "what is a monad"})]
    assert not w.hints.isVisible()          # hints clear once a query is running


def test_chunks_stream_into_response(qtbot):
    w, client = make(qtbot)
    w.input.setText("hi")
    qtbot.keyClick(w.input, Qt.Key.Key_Return)
    client.chunk.emit("Hel")
    client.chunk.emit("lo.")
    client.done.emit()
    assert w.response.toPlainText() == "Hello."
    assert w.response.isVisible()
    assert not w.status.isVisible()


def test_wake_state_appears_when_slow(qtbot):
    w, client = make(qtbot)
    w.input.setText("hi")
    qtbot.keyClick(w.input, Qt.Key.Key_Return)
    qtbot.wait(1700)                        # exceed the 1500ms cold-start threshold
    assert w.status.isVisible()
    assert "waking model" in w.status.text()
    client.chunk.emit("x")
    assert not w.status.isVisible()


def test_error_state(qtbot):
    w, client = make(qtbot)
    w.input.setText("hi")
    qtbot.keyClick(w.input, Qt.Key.Key_Return)
    client.error.emit("daemon offline — start it")
    assert w.status.isVisible()
    assert "daemon offline" in w.status.text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_launcher.py -v`
Expected: FAIL — `ImportError: cannot import name 'LauncherScreen'`.

- [ ] **Step 3: Implement `lumen/ui/launcher.py`**

```python
"""Quick-launcher: the one live screen in Phase 1. Streams answers from the
daemon; never blocks silently on a cold model load (visible 'waking' state)."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLineEdit, QTextEdit,
                             QVBoxLayout, QWidget)

from lumen.ui import theme
from lumen.ui.widgets import label

WAKE_THRESHOLD_MS = 1500

HINTS = [
    "what's on my calendar today",
    "add todo: renew domain by friday",
    "recommend a book like the last two I finished",
    "summarize unread email from Priya",
]


class LauncherScreen(QWidget):
    def __init__(self, client):
        super().__init__()
        self._client = client
        client.chunk.connect(self._on_chunk)
        client.done.connect(self._on_done)
        client.error.connect(self._on_error)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 12)
        root.setSpacing(12)

        inputrow = QHBoxLayout()
        caret = label("❯", "accent-eyebrow")
        caret.setStyleSheet(f"color: {theme.ACCENT}; font-size: 18px; font-weight: 700;")
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Lumen or type a command…")
        self.input.returnPressed.connect(self._submit)
        inputrow.addWidget(caret)
        inputrow.addWidget(self.input, 1)
        inputrow.addWidget(label("llm", "kbd"))
        root.addLayout(inputrow)

        self.hints = QWidget()
        hv = QVBoxLayout(self.hints)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.addWidget(label("TRY", "eyebrow"))
        for h in HINTS:
            row = QHBoxLayout()
            row.addWidget(label("❯", "accent-eyebrow"))
            row.addWidget(label(h, "secondary"), 1)
            row.addWidget(label("↵", "kbd"))
            hv.addLayout(row)
        root.addWidget(self.hints)

        self.status = label("", "status")
        self.status.hide()
        root.addWidget(self.status)

        self.response = QTextEdit()
        self.response.setReadOnly(True)
        self.response.hide()
        root.addWidget(self.response, 1)

        footer = QHBoxLayout()
        for hint in ("↵ run", "↑↓ navigate", "esc dismiss", "super+space to summon"):
            footer.addWidget(label(hint, "faint"))
        footer.addStretch()
        root.addLayout(footer)

        self._wake_timer = QTimer(self)
        self._wake_timer.setSingleShot(True)
        self._wake_timer.setInterval(WAKE_THRESHOLD_MS)
        self._wake_timer.timeout.connect(
            lambda: self._set_status("waking model… (cold start, a few seconds)"))

    def _submit(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.hints.hide()
        self.response.clear()
        self.response.hide()
        self.status.hide()
        self._wake_timer.start()
        self._client.send("chat", {"message": text})

    def _on_chunk(self, text: str) -> None:
        self._wake_timer.stop()
        self.status.hide()
        if not self.response.isVisible():
            self.response.show()
        self.response.moveCursor(self.response.textCursor().MoveOperation.End)
        self.response.insertPlainText(text)

    def _on_done(self) -> None:
        self._wake_timer.stop()
        self.status.hide()

    def _on_error(self, message: str) -> None:
        self._wake_timer.stop()
        self._set_status(message, error=True)

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.setProperty("role", "error" if error else "status")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)
        self.status.show()


class LauncherOverlay(QDialog):
    """Frameless hotkey-summoned overlay wrapping its own LauncherScreen."""

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setFixedWidth(620)
        self.setProperty("role", "overlay")
        v = QVBoxLayout(self)
        v.setContentsMargins(1, 1, 1, 1)
        self.screen_widget = LauncherScreen(client)
        v.addWidget(self.screen_widget)

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()
            self.screen_widget.input.setFocus()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_launcher.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/ui/test_launcher.py lumen/ui/launcher.py
git commit -m "Add live launcher: streaming responses with waking/error states, overlay variant

This commit used 3 prompts."
```

---

### Task 19: Tray, single instance, UI entrypoint, end-to-end gate

**Files:**
- Modify: `lumen/ui/tray.py` (replace stub comment)
- Create: `lumen/ui/single_instance.py`, `lumen/ui/__main__.py`, `tests/ui/test_single_instance.py`

**Interfaces:**
- Consumes: `MainWindow` (Task 10), all screens (Tasks 11–16, 18), `DaemonClient` (Task 17), `default_socket_path` (Task 2).
- Produces: `lumen-ui` console script — single-instance app; `--toggle-launcher` toggles the overlay of the running instance (or starts one). `make_tray(app, on_show, on_toggle_launcher, on_sleep) -> QSystemTrayIcon`. `single_instance.try_send(name: str, cmd: str) -> bool` and `class InstanceServer(QObject)` with signal `message(str)`.

- [ ] **Step 1: Write the failing test**

`tests/ui/test_single_instance.py`:

```python
from lumen.ui.single_instance import InstanceServer, try_send


def test_second_instance_hands_off_command(qtbot):
    name = "lumen-ui-test"
    assert try_send(name, "toggle-launcher") is False   # nobody listening yet
    server = InstanceServer(name)
    with qtbot.waitSignal(server.message, timeout=2000) as blocker:
        assert try_send(name, "toggle-launcher") is True
    assert blocker.args == ["toggle-launcher"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_single_instance.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `lumen/ui/single_instance.py`**

```python
"""Single-instance guard. A second `lumen-ui` invocation hands its command to
the running instance over a QLocalServer and exits — this is how the Hyprland
hotkey toggles the launcher."""

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

SOCKET_NAME = "lumen-ui"


def try_send(name: str, cmd: str) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(500):
        return False
    sock.write(cmd.encode() + b"\n")
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    message = pyqtSignal(str)

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        QLocalServer.removeServer(name)  # stale socket from a crashed run
        self._server = QLocalServer(self)
        self._server.listen(name)
        self._server.newConnection.connect(self._on_conn)

    def _on_conn(self) -> None:
        conn = self._server.nextPendingConnection()
        conn.readyRead.connect(
            lambda: self.message.emit(bytes(conn.readLine()).decode().strip()))
```

- [ ] **Step 4: Implement `lumen/ui/tray.py`**

```python
"""Tray icon + menu (SNI — shows up in waybar's tray module)."""

from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from lumen.ui import theme


def _icon() -> QIcon:
    pm = QPixmap(22, 22)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(theme.ACCENT))
    p.setPen(QColor(theme.ACCENT))
    p.drawEllipse(4, 4, 14, 14)
    p.end()
    return QIcon(pm)


def make_tray(app: QApplication, on_show, on_toggle_launcher, on_sleep) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(_icon(), app)
    menu = QMenu()
    menu.addAction("Show Lumen", on_show)
    menu.addAction("Toggle launcher", on_toggle_launcher)
    menu.addSeparator()
    menu.addAction("Sleep model now", on_sleep)
    menu.addSeparator()
    menu.addAction("Quit", app.quit)
    tray.setContextMenu(menu)
    tray.setToolTip("Lumen — daily assistant")
    tray.show()
    return tray
```

- [ ] **Step 5: Implement `lumen/ui/__main__.py`**

```python
"""UI entrypoint: single-instance PyQt6 app. `--toggle-launcher` either messages
the running instance or starts one with the overlay shown."""

import sys

from PyQt6.QtWidgets import QApplication

from lumen.daemon.config import default_socket_path
from lumen.ui.book_catalog import BooksScreen
from lumen.ui.calendar_view import CalendarScreen
from lumen.ui.daemon_client import DaemonClient
from lumen.ui.dashboard import DashboardScreen
from lumen.ui.launcher import LauncherOverlay, LauncherScreen
from lumen.ui.mail import MailScreen
from lumen.ui.settings import SettingsScreen
from lumen.ui.shell import MainWindow
from lumen.ui.single_instance import SOCKET_NAME, InstanceServer, try_send
from lumen.ui.theme import build_qss
from lumen.ui.todo_manager import TodoScreen
from lumen.ui.tray import make_tray


def main() -> None:
    toggle = "--toggle-launcher" in sys.argv
    cmd = "toggle-launcher" if toggle else "show"
    if try_send(SOCKET_NAME, cmd):
        return  # running instance handled it

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(build_qss())

    socket_path = str(default_socket_path())
    tab_client = DaemonClient(socket_path)
    overlay_client = DaemonClient(socket_path)

    win = MainWindow({
        "launcher": LauncherScreen(tab_client),
        "dashboard": DashboardScreen(),
        "calendar": CalendarScreen(),
        "mail": MailScreen(),
        "todos": TodoScreen(),
        "books": BooksScreen(),
        "settings": SettingsScreen(),
    })
    overlay = LauncherOverlay(overlay_client)
    win.sleep_requested.connect(tab_client.sleep_model)

    def dispatch(command: str) -> None:
        if command == "toggle-launcher":
            overlay.toggle()
        else:
            win.show()
            win.raise_()
            win.activateWindow()

    server = InstanceServer(SOCKET_NAME)
    server.message.connect(dispatch)

    make_tray(app, on_show=lambda: dispatch("show"),
              on_toggle_launcher=overlay.toggle,
              on_sleep=tab_client.sleep_model)

    if toggle:
        overlay.toggle()
    else:
        win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -v`
Expected: all tests pass (daemon 13, ui ~20). No skips other than none expected.

- [ ] **Step 7: End-to-end manual gate (Phase 1 success criterion)**

1. `uv run lumen-daemon` in one terminal.
2. `uv run lumen-ui` in another — the shell opens on the Launcher tab; tabs 1–6 and the gear all navigate; every screen shows its skeleton.
3. Type a general-knowledge question ("why is the sky blue") → "waking model…" appears briefly (cold load) → a real streamed answer renders.
4. `ollama ps` → model resident. Wait past the keep-alive window (or use the 1m override from `docs/ollama-setup.md` §5) → `ollama ps` empty. **Model demonstrably unloads.**
5. `uv run lumen-ui --toggle-launcher` from a third terminal → the overlay toggles in the running instance.
6. Stop the daemon, ask again → launcher shows the "daemon offline" hint instead of hanging.

Record actual cold-start seconds observed in the commit message body.

- [ ] **Step 8: Commit**

```bash
git add lumen/ui/tray.py lumen/ui/single_instance.py lumen/ui/__main__.py tests/ui/test_single_instance.py
git commit -m "Add tray, single-instance toggle, and UI entrypoint; Phase 1 end-to-end verified

Cold start observed: <fill in>s to first token on qwen3:4b.

This commit used 3 prompts."
```

---

## Self-Review Notes

- **Spec coverage:** walkthrough doc (Task 7), daemon skeleton (Tasks 2–6), UI shell + six screens (Tasks 10–16), launcher live with states (Task 18), confirmation dialog component (Task 9), tray + hotkey path (Task 19 + doc §7), idle-unload enforcement (Tasks 3, 7), error handling (Tasks 3–5, 17, 18), testing (every task). Accent-picker UI, connectors, MCP: out of scope per spec.
- **Type consistency check:** `Config.keep_alive: str` ("10m") vs `unload()`'s `keep_alive: 0` (int) — intentional; Ollama accepts both forms. `Router` consumes `chat`/`unload`; `IPCServer` consumes `handle`; `DaemonClient` emits `chunk/done/error` which `LauncherScreen` consumes; names verified consistent across tasks.
- **Known simplification:** screens are static skeletons with fixture data from the mockups; pixel parity is Phase 10 by design.
