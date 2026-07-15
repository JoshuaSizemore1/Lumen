"""Daemon configuration. Non-secret settings from config.toml; secrets stay in .env."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def default_socket_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / "lumen" / "daemon.sock"
    return Path(f"/tmp/lumen-{os.getuid()}") / "daemon.sock"


def default_db_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "lumen" / "lumen.db"


def default_tool_log_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "lumen" / "tool-calls.jsonl"


def default_grants_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "lumen" / "write-grants.txt"


def default_style_rules_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "lumen" / "writing-style.md"


def default_memory_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "lumen" / "memory.md"


def default_procedures_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "lumen" / "procedures"


def default_google_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "lumen" / "google"


@dataclass(frozen=True)
class GoogleConfig:
    client_secret_path: Path = field(
        default_factory=lambda: default_google_dir() / "client_secret.json")
    token_path: Path = field(
        default_factory=lambda: default_google_dir() / "token.json")


@dataclass(frozen=True)
class ManabiConfig:
    # Japanese-study nudge: path to Manabi's SQLite DB (read-only). None =
    # feature off; Lumen never nags without a readable signal.
    db_path: Path | None = None


@dataclass(frozen=True)
class NotesConfig:
    # Notes Q&A folder (user decision 2026-07-13: ~/Documents/Notes, created
    # if absent). Markdown/plain text only. Embedding model benchmarked
    # 2026-07-14 (see llm-serving.md): nomic-embed-text.
    folder: Path = field(default_factory=lambda: Path.home() / "Documents" / "Notes")
    embed_model: str = "nomic-embed-text"


@dataclass(frozen=True)
class SchedulingConfig:
    # Proposable hours for NL scheduling (user decision 2026-07-13:
    # 8:00–20:00 local, weekends included).
    day_start: str = "08:00"
    day_end: str = "20:00"


@dataclass(frozen=True)
class MemoryConfig:
    # Two-tier personalization memory (Phase 9). Caps are hard limits, not
    # targets — the distiller compresses/drops to stay under them.
    blob_cap_chars: int = 4000          # memory.md — ~1k tokens, 4B budget
    distill_min_entries: int = 20       # unfolded log rows that force a run
    distill_soft_entries: int = 5       # fewer rows still run if the oldest is old
    distill_soft_age_hours: int = 24
    distill_cooldown_hours: int = 24    # min gap between runs
    distill_delay_seconds: int = 120    # ride the warm model ~2 min after a chat
    decay_days: int = 60                # bullets older than this are dropped
    procedure_cap_chars: int = 1000
    max_active_procedures: int = 10
    procedure_retire_days: int = 90


@dataclass(frozen=True)
class SyncConfig:
    calendar_poll_minutes: int = 5
    calendar_window_past_days: int = 30
    calendar_window_future_days: int = 60
    gmail_poll_minutes: int = 5
    gmail_window_months: int = 6


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str
    args: tuple[str, ...] = ()
    tools: tuple[str, ...] | None = None   # read-only allowlist; None = expose all
    # tool name -> path-argument names; presence marks a tool write-capable,
    # gated by the daemon's grant check (never guessed from names at runtime)
    write_tools: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPConfig:
    enabled: bool = False
    max_iterations: int = 4
    log_path: Path = field(default_factory=default_tool_log_path)
    grants_path: Path = field(default_factory=default_grants_path)
    servers: tuple[MCPServerConfig, ...] = ()


@dataclass(frozen=True)
class Config:
    model: str = "qwen3:4b-instruct"
    escalation_model: str | None = None   # 14B-class slot; unset until benchmarked
    idle_unload_minutes: int = 10
    ollama_url: str = "http://127.0.0.1:11434"
    think: bool = False
    socket_path: Path = field(default_factory=default_socket_path)
    db_path: Path = field(default_factory=default_db_path)
    mcp: "MCPConfig" = field(default_factory=lambda: MCPConfig())
    google: "GoogleConfig" = field(default_factory=lambda: GoogleConfig())
    sync: "SyncConfig" = field(default_factory=lambda: SyncConfig())
    scheduling: "SchedulingConfig" = field(default_factory=lambda: SchedulingConfig())
    notes: "NotesConfig" = field(default_factory=lambda: NotesConfig())
    manabi: "ManabiConfig" = field(default_factory=lambda: ManabiConfig())
    memory: "MemoryConfig" = field(default_factory=lambda: MemoryConfig())
    memory_path: Path = field(default_factory=default_memory_path)
    procedures_dir: Path = field(default_factory=default_procedures_dir)

    @property
    def keep_alive(self) -> str:
        return f"{self.idle_unload_minutes}m"


def _parse_hhmm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(value)
    return h, m


def load_config(path: Path | None = None) -> Config:
    path = path or Path(__file__).resolve().parent.parent / "config.toml"
    if not path.exists():
        return Config()
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise SystemExit(f"lumen: invalid TOML in {path}: {e}")
    llm = data.get("llm", {})
    ipc = data.get("ipc", {})
    kwargs = {}
    if "model" in llm:
        kwargs["model"] = llm["model"]
    if "escalation_model" in llm:
        kwargs["escalation_model"] = llm["escalation_model"]
    if "idle_unload_minutes" in llm:
        kwargs["idle_unload_minutes"] = llm["idle_unload_minutes"]
    if "ollama_url" in llm:
        kwargs["ollama_url"] = llm["ollama_url"]
    if "think" in llm:
        kwargs["think"] = llm["think"]
    if "socket_path" in ipc:
        kwargs["socket_path"] = Path(ipc["socket_path"])
    storage = data.get("storage", {})
    if "db_path" in storage:
        kwargs["db_path"] = Path(storage["db_path"]).expanduser()
    mcp_raw = data.get("mcp")
    if mcp_raw is not None:
        servers = []
        for s in mcp_raw.get("servers", []):
            if "name" not in s or "command" not in s:
                raise SystemExit("lumen: each [[mcp.servers]] entry needs name and command")
            write_tools = {}
            for tool, path_args in s.get("write_tools", {}).items():
                if (not isinstance(path_args, list) or not path_args
                        or not all(isinstance(a, str) for a in path_args)):
                    raise SystemExit(
                        f"lumen: write_tools.{tool} needs its path argument names "
                        "as a non-empty list of strings")
                write_tools[tool] = tuple(path_args)
            servers.append(MCPServerConfig(
                name=s["name"],
                command=s["command"],
                args=tuple(s.get("args", [])),
                tools=tuple(s["tools"]) if "tools" in s else None,
                write_tools=write_tools,
            ))
        servers = tuple(servers)
        mcp_kwargs = {"enabled": bool(mcp_raw.get("enabled", False)), "servers": servers}
        if "max_iterations" in mcp_raw:
            mcp_kwargs["max_iterations"] = int(mcp_raw["max_iterations"])
        if mcp_raw.get("log_path"):
            mcp_kwargs["log_path"] = Path(mcp_raw["log_path"]).expanduser()
        if mcp_raw.get("grants_path"):
            mcp_kwargs["grants_path"] = Path(mcp_raw["grants_path"]).expanduser()
        if mcp_kwargs.get("max_iterations", 4) <= 0:
            raise SystemExit("lumen: [mcp] max_iterations must be positive")
        kwargs["mcp"] = MCPConfig(**mcp_kwargs)
    google_raw = data.get("google")
    if google_raw is not None:
        g_kwargs = {}
        if "client_secret_path" in google_raw:
            g_kwargs["client_secret_path"] = Path(google_raw["client_secret_path"]).expanduser()
        if "token_path" in google_raw:
            g_kwargs["token_path"] = Path(google_raw["token_path"]).expanduser()
        kwargs["google"] = GoogleConfig(**g_kwargs)
    sync_raw = data.get("sync")
    if sync_raw is not None:
        s_kwargs = {k: int(sync_raw[k]) for k in
                    ("calendar_poll_minutes", "calendar_window_past_days",
                     "calendar_window_future_days", "gmail_poll_minutes",
                     "gmail_window_months") if k in sync_raw}
        sync_cfg = SyncConfig(**s_kwargs)
        if sync_cfg.calendar_poll_minutes < 5 or sync_cfg.gmail_poll_minutes < 5:
            raise SystemExit(
                "lumen: poll minutes must be at least 5 — no tight polling loops")
        if (sync_cfg.calendar_window_past_days < 0
                or sync_cfg.calendar_window_future_days < 0):
            raise SystemExit("lumen: calendar sync window days must be non-negative")
        if sync_cfg.gmail_window_months < 1:
            raise SystemExit("lumen: gmail_window_months must be at least 1")
        kwargs["sync"] = sync_cfg
    manabi_raw = data.get("manabi")
    if manabi_raw is not None and "db_path" in manabi_raw:
        kwargs["manabi"] = ManabiConfig(
            db_path=Path(manabi_raw["db_path"]).expanduser())
    notes_raw = data.get("notes")
    if notes_raw is not None:
        n_kwargs = {}
        if "folder" in notes_raw:
            n_kwargs["folder"] = Path(notes_raw["folder"]).expanduser()
        if "embed_model" in notes_raw:
            n_kwargs["embed_model"] = str(notes_raw["embed_model"])
        kwargs["notes"] = NotesConfig(**n_kwargs)
    sched_raw = data.get("scheduling")
    if sched_raw is not None:
        sched_kwargs = {k: str(sched_raw[k]) for k in ("day_start", "day_end")
                        if k in sched_raw}
        sched = SchedulingConfig(**sched_kwargs)
        try:
            start_t = _parse_hhmm(sched.day_start)
            end_t = _parse_hhmm(sched.day_end)
        except ValueError:
            raise SystemExit("lumen: [scheduling] day_start/day_end must be HH:MM")
        if start_t >= end_t:
            raise SystemExit("lumen: [scheduling] day_start must be before day_end")
        kwargs["scheduling"] = sched
    mem_raw = data.get("memory")
    if mem_raw is not None:
        int_fields = ("blob_cap_chars", "distill_min_entries",
                      "distill_soft_entries", "distill_soft_age_hours",
                      "distill_cooldown_hours", "distill_delay_seconds",
                      "decay_days", "procedure_cap_chars",
                      "max_active_procedures", "procedure_retire_days")
        m_kwargs = {k: int(mem_raw[k]) for k in int_fields if k in mem_raw}
        mem_cfg = MemoryConfig(**m_kwargs)
        if mem_cfg.blob_cap_chars <= 0 or mem_cfg.max_active_procedures <= 0:
            raise SystemExit("lumen: [memory] caps must be positive")
        kwargs["memory"] = mem_cfg
    if "memory_path" in storage:
        kwargs["memory_path"] = Path(storage["memory_path"]).expanduser()
    if "procedures_dir" in storage:
        kwargs["procedures_dir"] = Path(storage["procedures_dir"]).expanduser()
    idle_unload_minutes = kwargs.get("idle_unload_minutes", Config.idle_unload_minutes)
    if idle_unload_minutes <= 0:
        raise SystemExit(
            f"lumen: idle_unload_minutes must be positive (got {idle_unload_minutes}) "
            "— idle-unload is non-negotiable"
        )
    return Config(**kwargs)
