"""Claude CLI backend. Spawns `claude -p` as a short-lived subprocess for each
request. System messages become --system-prompt; the rest of the conversation
is a labelled transcript on stdin. Tool use is structured-output-based, keeping
the loop in the daemon like the Ollama path.

Errors become ClaudeUnavailable (a LLMUnavailable subclass) with a .reason
code so callers can distinguish "logged out, go to Settings" from "offline"."""

import asyncio
import contextvars
import datetime
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from pathlib import Path

from lumen.daemon.llm.client import LLMUnavailable, _describe_results

log = logging.getLogger("lumen.daemon")

# Salvage constants (same semantics as OllamaClient #45)
NO_MODEL_ANSWER = (
    "Claude returned nothing for that. Asking again, or rephrasing it, "
    "usually works."
)
_ANSWER_FROM_RESULTS = (
    "Answer the question now, in plain sentences, using only the tool results "
    "above. Do not call any more tools. If the results do not contain the "
    "answer, say exactly what they do contain."
)


# Set by the background loops (Canvas enrichment, memory distill) around their
# model work, so their CLI calls yield the semaphore to anything the user is
# waiting on. A context var rather than a kwarg: the feature modules stay
# backend-agnostic, and OllamaClient.chat takes no such argument.
BACKGROUND = contextvars.ContextVar("lumen_llm_background", default=False)


# Plain (tool-less) calls only. Lumen's identity says "use your tools"; with
# none attached, Haiku typed fake <function_calls> markup as its answer (live,
# 2026-09-24). The 4B never did this, so the fix lives in the Claude client.
# Kept to one line on purpose: chat() also serves strict-format calls (intent
# labels, triage, label suggestions), and a longer "explain and suggest" note
# talked Haiku into prose there.
_NO_TOOLS_NOTE = ("No tools are attached to this reply: never write "
                  "function-call, tool-call, or XML markup.")


class ClaudeUnavailable(LLMUnavailable):
    """Claude CLI is unavailable or returned an error.

    .reason is one of: not_installed | logged_out | rate_limited | offline |
    timeout | error."""

    def __init__(self, message: str, reason: str = "error"):
        super().__init__(message)
        self.reason = reason


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    d = root / "lumen" / "claude-cwd"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)          # holds the per-call system-prompt files
    return d


def _split_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Partition: system role → system_prompt string, rest → transcript list."""
    sys_parts, rest = [], []
    for m in messages:
        if m.get("role") == "system":
            sys_parts.append(str(m.get("content") or ""))
        else:
            rest.append(m)
    return "\n\n".join(sys_parts), rest


def _render_transcript(turns: list[dict]) -> str:
    """Labelled-transcript format for the single stdin prompt.

    role=user      → [User]\\ncontent
    role=assistant → [Assistant]\\ncontent (or tool_calls json for prior iters)
    role=tool      → [Tool result: name]\\ncontent
    """
    parts = []
    for m in turns:
        role = m.get("role", "")
        content = m.get("content") or ""
        if role == "user":
            parts.append(f"[User]\n{content}")
        elif role == "assistant":
            parts.append(f"[Assistant]\n{content}")
        elif role == "tool_request":
            parts.append(f"[Assistant requested lookup: {m.get('tool_name')}]\n"
                         f"{json.dumps(m.get('arguments') or {})}")
        elif role == "tool":
            name = str(m.get("tool_name") or "tool")
            parts.append(f"[Tool result: {name}]\n{content}")
    return "\n\n".join(parts)


def _tool_system_addendum(tools: list[dict]) -> str:
    """Tool descriptions + calling framing appended to the system prompt.

    Phrasing validated in Phase 0: Haiku does NOT attempt native tool calls
    when told "you cannot call any tool directly"."""
    names = [t.get("function", {}).get("name", "") for t in tools]
    descs = []
    for t in tools:
        fn = t.get("function", {})
        name = fn.get("name", "")
        desc = fn.get("description", "")
        params = fn.get("parameters") or {}
        props = params.get("properties") or {}
        req = set(params.get("required") or [])
        plines = []
        for pname, pinfo in props.items():
            opt = "" if pname in req else " (optional)"
            plines.append(f"  - {pname} ({pinfo.get('type','string')}{opt}): "
                          f"{pinfo.get('description','')}")
        params_str = "\n".join(plines) if plines else "  (no parameters)"
        descs.append(f"Tool: {name}\nDescription: {desc}\nParameters:\n{params_str}")

    framing = (
        "You have access to the following tools. You cannot call any tool "
        "directly. Instead, return them in tool_calls in the structured output. "
        "The host runs them and replies with the results. "
        "When you have what you need, return answer and no tool_calls."
    )
    # Repeated last: after ~10 KB of identity, memory and schemas, the opening
    # framing alone lost to the identity's "use your tools" (live 2026-09-24).
    closing = (
        "HOW LOOKUPS WORK HERE: every tool above is available and working, but "
        "only through the tool_calls field of your structured output — never "
        "as a direct function call. List the lookups you need in tool_calls "
        "with answer left empty; the host runs them and sends back the results. "
        "Never tell the user a tool is unavailable."
    )
    return (framing + "\n\nAvailable tools:\n\n" + "\n\n".join(descs)
            + "\n\n" + closing)


def _tool_schema(tool_names: list[str]) -> str:
    """--json-schema value for one structured-output tool-loop iteration.

    Enuming the tool names in the schema is required (Phase 0 finding): without
    the enum Haiku tries to call tools as native tools, which fail."""
    schema = {
        "type": "object",
        "properties": {
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": tool_names},
                        "arguments": {"type": "object"},
                    },
                    "required": ["name", "arguments"],
                },
            },
            "answer": {"type": "string"},
        },
    }
    return json.dumps(schema)


# Where the official installers put `claude`. A daemon started from the desktop
# or systemd inherits a PATH without ~/.local/bin, so PATH alone reports a
# perfectly good install as missing (Josh hit this, 2026-09-24).
_CLI_FALLBACKS = (
    "~/.local/bin/claude",
    "~/.claude/local/claude",
    "~/.npm-global/bin/claude",
    "/usr/local/bin/claude",
)


def _resolve_cli(cli_path: str) -> str | None:
    """Absolute path of the claude executable, or None if it can't be found."""
    if os.sep in cli_path:
        path = os.path.expanduser(cli_path)
        return path if os.access(path, os.X_OK) else None
    found = shutil.which(cli_path)
    if found:
        return found
    if cli_path != "claude":
        return None
    for cand in _CLI_FALLBACKS:
        path = os.path.expanduser(cand)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


class ClaudeCliClient:
    """Claude CLI-backed LLM client. Interface matches OllamaClient."""

    def __init__(
        self,
        model: str,
        cli_path: str = "claude",
        timeout_seconds: int = 120,
        max_concurrent: int = 2,
    ):
        self.model = model
        self._cli_path = cli_path
        self._timeout = timeout_seconds
        self._sem = asyncio.Semaphore(max_concurrent)
        # Priority: interactive callers increment this; background waits for 0.
        self._interactive_waiters = 0
        self._cwd: Path | None = None
        # last rate_limit_event.unifiedWindows snapshot
        self.last_usage: dict | None = None
        # claude auth status cache: (monotonic_ts, result_dict)
        self._auth_cache: tuple[float, dict] | None = None
        self._auth_ttl = 60.0

    def _ensure_cwd(self) -> Path:
        if self._cwd is None:
            self._cwd = _state_dir()
        return self._cwd

    async def _acquire(self, background: bool) -> None:
        background = background or BACKGROUND.get()
        if not background:
            self._interactive_waiters += 1
        try:
            while True:
                if background:
                    # Back off until no interactive caller is waiting.
                    while self._interactive_waiters > 0:
                        await asyncio.sleep(0.05)
                await self._sem.acquire()
                # A background caller already queued on the semaphore would
                # otherwise win the slot over a user who asked after it did:
                # hand it straight to the waiting interactive caller instead.
                if not background or self._interactive_waiters == 0:
                    return
                self._sem.release()
                await asyncio.sleep(0)
        finally:
            if not background:
                self._interactive_waiters -= 1

    def _release(self) -> None:
        self._sem.release()

    def _argv_base(self, system_prompt_file: Path) -> list[str]:
        # The system prompt goes by FILE: Lumen's carries mail/calendar/todo
        # context and the memory blob, and argv is readable in `ps` by every
        # local user for the life of the call (review finding, 2026-09-24).
        return [
            _resolve_cli(self._cli_path) or self._cli_path, "-p",
            "--model", self.model,
            "--system-prompt-file", str(system_prompt_file),
            "--tools", "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--settings", '{"alwaysThinkingEnabled":false}',
            "--output-format", "stream-json",
            "--verbose",
        ]

    async def _spawn(self, argv: list[str]) -> asyncio.subprocess.Process:
        cwd = self._ensure_cwd()
        try:
            return await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(cwd),
                # One stream-json line can carry a whole answer or tool result;
                # asyncio's 64 KiB readline default would raise mid-stream.
                limit=16 * 1024 * 1024,
            )
        except FileNotFoundError:
            raise ClaudeUnavailable(
                "Claude CLI isn't installed — install it from https://claude.ai/cli",
                reason="not_installed",
            )

    @staticmethod
    def _parse(line: bytes) -> dict | None:
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None

    def _check_error(self, obj: dict) -> None:
        """Raise ClaudeUnavailable if obj signals an error."""
        t = obj.get("type")
        if t == "assistant":
            msg = obj.get("message") or {}
            err = msg.get("error") if isinstance(msg, dict) else None
            if err == "authentication_failed":
                raise ClaudeUnavailable(
                    "Claude CLI is logged out — run `claude auth login` in a terminal",
                    reason="logged_out",
                )
            if err:
                raise ClaudeUnavailable(
                    f"Claude error ({err}): "
                    f"{_first_text(msg)}",
                    reason="error",
                )
        elif t == "result" and obj.get("is_error"):
            text = str(obj.get("result") or "Claude returned an error")
            lo = text.lower()
            if "not logged in" in lo or "authentication_failed" in lo or "run /login" in lo:
                raise ClaudeUnavailable(
                    "Claude CLI is logged out — run `claude auth login` in a terminal",
                    reason="logged_out",
                )
            if "rate limit" in lo or "rate_limit" in lo:
                raise ClaudeUnavailable(
                    "Claude usage limit reached — try again later",
                    reason="rate_limited",
                )
            raise ClaudeUnavailable(text[:300], reason="error")

    def _handle_rate_limit(self, obj: dict) -> None:
        """Cache the unifiedWindows snapshot; raise if status != allowed."""
        if obj.get("type") != "rate_limit_event":
            return
        info = obj.get("rate_limit_info") or {}
        windows = info.get("unifiedWindows") or {}
        if windows:
            usage = {}
            for key, val in windows.items():
                if isinstance(val, dict):
                    usage[key] = {
                        "utilization": float(val.get("utilization", 0.0)),
                        "resets_at": int(val.get("resetsAt", 0)),
                    }
            if usage:
                self.last_usage = usage
        # "allowed_warning" (live at 90% of the 5h window, 2026-09-24) still
        # answers — treating it as a limit would refuse requests that succeed.
        if not str(info.get("status") or "allowed").startswith("allowed"):
            resets = min(
                (w.get("resetsAt", 0) for w in windows.values() if isinstance(w, dict)),
                default=0,
            )
            if resets:
                reset_str = datetime.datetime.fromtimestamp(resets).strftime("%I:%M %p")
                msg = f"Claude usage limit reached — resets at {reset_str}"
            else:
                msg = "Claude usage limit reached — try again later"
            raise ClaudeUnavailable(msg, reason="rate_limited")

    def _process_obj(self, obj: dict) -> None:
        """Handle non-content events (errors, rate limits)."""
        self._handle_rate_limit(obj)
        self._check_error(obj)

    async def chat(self, messages: list[dict], *, background: bool = False) -> AsyncIterator[str]:
        """Stream text chunks. Prompt goes on stdin, never in argv."""
        system, rest = _split_messages(messages)
        system = (system + "\n\n" + _NO_TOOLS_NOTE) if system else _NO_TOOLS_NOTE
        stdin_data = (str(rest[0].get("content") or "")
                      if len(rest) == 1 and rest[0].get("role") == "user"
                      else _render_transcript(rest))
        async with aclosing(self._events(system, stdin_data, background,
                                         ["--include-partial-messages"])) as events:
            async for obj in events:
                if obj.get("type") == "stream_event":
                    ev = obj.get("event", {})
                    if (ev.get("type") == "content_block_delta"
                            and ev.get("delta", {}).get("type") == "text_delta"):
                        text = ev["delta"].get("text", "")
                        if text:
                            yield text

    async def _events(self, system: str, stdin_data: str, background: bool,
                      extra: list[str]) -> AsyncIterator[dict]:
        """One CLI run: parsed stream-json objects, errors already raised.

        Owns everything that must not leak: the semaphore slot, the private
        system-prompt file, and the process (killed + reaped on every exit,
        including a consumer that stops early). The timeout covers the stdin
        write too — a CLI stuck before reading its input would otherwise hang
        the exclusive chat route, and with it every gated route, forever."""
        await self._acquire(background)
        proc = sp_path = None
        try:
            fd, sp_path = tempfile.mkstemp(prefix="sp-", suffix=".txt",
                                           dir=self._ensure_cwd())   # 0600
            with os.fdopen(fd, "w") as f:
                f.write(system)
            proc = await self._spawn(self._argv_base(Path(sp_path)) + extra)
            try:
                async with asyncio.timeout(self._timeout):
                    proc.stdin.write(stdin_data.encode())
                    await proc.stdin.drain()
                    proc.stdin.close()
                    async for line in proc.stdout:
                        obj = self._parse(line)
                        if obj is None:
                            continue
                        self._process_obj(obj)
                        yield obj
            except asyncio.TimeoutError:
                raise ClaudeUnavailable("Claude took too long — try again",
                                        reason="timeout")
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            if proc is not None:
                await proc.wait()
            if sp_path is not None:
                try:
                    os.unlink(sp_path)
                except OSError:
                    pass
            self._release()

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        executor: Callable[[str, dict], Awaitable[str]],
        *,
        model: str | None = None,
        max_iterations: int = 4,
        background: bool = False,
    ) -> AsyncIterator[dict]:
        """Structured-output tool loop. Each iteration is one CLI invocation."""
        system, rest = _split_messages(messages)
        aug_system = (system + "\n\n" + _tool_system_addendum(tools)
                      if system else _tool_system_addendum(tools))
        tool_names = [t.get("function", {}).get("name", "") for t in tools]
        schema = _tool_schema(tool_names)

        convo = list(rest)

        for _ in range(max_iterations):
            stdin_data = (str(convo[0].get("content") or "")
                          if len(convo) == 1 and convo[0].get("role") == "user"
                          else _render_transcript(convo))

            structured = await self._run_json_call(aug_system, stdin_data, schema,
                                                   background, frozenset(tool_names))

            if structured is None:
                # No structured output — salvage from tool results seen so far.
                tool_msgs = [m for m in convo if m.get("role") == "tool"]
                log.warning("claude tool loop: no structured output after %d call(s)",
                            len(tool_msgs))
                yield {"content": _salvage(tool_msgs)}
                return

            calls = structured.get("tool_calls") or []
            answer = (structured.get("answer") or "").strip()

            if not calls:
                if answer:
                    yield {"content": answer}
                else:
                    tool_msgs = [m for m in convo if m.get("role") == "tool"]
                    log.warning("claude tool loop: empty answer, %d prior calls",
                                len(tool_msgs))
                    yield {"content": _salvage(tool_msgs)}
                return

            for call in calls:
                name = str(call.get("name") or "")
                args = call.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                yield {"tool_call": {"name": name, "arguments": args}}
                # Record the request too, so the next iteration sees which
                # arguments produced each result (Phase 0's validated shape).
                convo.append({"role": "tool_request", "tool_name": name,
                              "arguments": args})
                result_text = await executor(name, args)
                convo.append({"role": "tool", "content": result_text,
                               "tool_name": name})

        # Exhausted iterations without a clean answer.
        tool_msgs = [m for m in convo if m.get("role") == "tool"]
        yield {"content": _salvage(tool_msgs), "capped": True}

    async def _run_json_call(self, system: str, stdin_data: str, schema: str,
                              background: bool,
                              offered: frozenset[str] = frozenset()) -> dict | None:
        """One CLI invocation; returns structured_output or None.

        Despite the framing, Haiku sometimes calls an offered tool natively
        (live, 2026-09-24: Lumen's identity prompt says "use your tools"). The
        CLI has no such tool, so it errors, and the model then tells the user
        the lookup "isn't available" and answers from memory. Those native
        calls are the model's real request: harvest them as tool_calls and
        stop the run as soon as the CLI answers them (the next "user" event),
        before the model gives up."""
        harvested: list[dict] = []
        async with aclosing(self._events(system, stdin_data, background,
                                         ["--json-schema", schema])) as events:
            async for obj in events:
                t = obj.get("type")
                if t == "user" and harvested:
                    return {"tool_calls": harvested}
                if t == "assistant":
                    for block in (obj.get("message") or {}).get("content") or []:
                        if (isinstance(block, dict)
                                and block.get("type") == "tool_use"
                                and block.get("name") in offered):
                            harvested.append({"name": block["name"],
                                              "arguments": block.get("input") or {}})
                if (t == "result" and not obj.get("is_error")
                        and obj.get("structured_output") is not None):
                    return obj["structured_output"]
        return {"tool_calls": harvested} if harvested else None

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        # Embeddings always route to Ollama (LLMBackend facade does this).
        raise LLMUnavailable(
            "Claude mode does not support embeddings — configure Ollama for notes search")

    async def is_loaded(self) -> bool:
        return True   # no cold start in Claude mode

    async def warm(self, prime: list[dict] | None = None) -> None:
        pass  # no-op: nothing to preload

    async def unload(self) -> None:
        pass  # no-op: no resident model

    async def aclose(self) -> None:
        pass  # no long-lived subprocess

    @property
    def last_status(self) -> dict | None:
        """Cached auth status dict, or None if never probed."""
        return self._auth_cache[1] if self._auth_cache is not None else None

    async def status(self) -> dict:
        """Return CLI installation and auth status. Short-TTL cache.
        Called off the hot path (settings.get) — never holds up chat."""
        now = time.monotonic()
        if (self._auth_cache is not None
                and (now - self._auth_cache[0]) < self._auth_ttl):
            return self._auth_cache[1]
        cli = _resolve_cli(self._cli_path)
        result: dict = {
            "installed": cli is not None,
            "logged_in": None,
            "account": None,
        }
        if cli is not None:
            try:
                proc = await asyncio.create_subprocess_exec(
                    cli, "auth", "status", "--json",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=str(self._ensure_cwd()),
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
                data = json.loads(stdout.decode())
                result["logged_in"] = bool(data.get("loggedIn"))
                result["account"] = data.get("email")
            except Exception:
                result["logged_in"] = None
        self._auth_cache = (now, result)
        return result


# ── helpers ───────────────────────────────────────────────────────────────────

def _first_text(msg: dict) -> str:
    """Extract the first text from an assistant message content list."""
    for block in msg.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text") or "")[:200]
    return ""


def _salvage(tool_msgs: list[dict]) -> str:
    """Last-resort answer from tool results (same semantics as OllamaClient #45)."""
    if tool_msgs:
        return _describe_results(tool_msgs)
    return NO_MODEL_ANSWER
