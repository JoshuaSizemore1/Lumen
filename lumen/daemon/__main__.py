"""Daemon entrypoint: config -> db -> Ollama client -> router -> IPC server,
until SIGINT/SIGTERM."""

import asyncio
import logging
import signal
import sys

from lumen.daemon import db
from lumen.daemon.config import load_config
from lumen.daemon.confirm import ConfirmBroker
from lumen.daemon.connectors.books import BookStore
from lumen.daemon.connectors.canvas_alerts import CanvasAlerts
from lumen.daemon.connectors.canvas_prefs import CanvasPrefs
from lumen.daemon.connectors.canvas_proposals import CanvasProposals
from lumen.daemon.connectors.canvas_queue import CanvasQueue
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.connectors.canvas_sync import CanvasSync
from lumen.daemon.connectors.connection_state import ConnectionState
from lumen.daemon.connectors.conversations import ConversationStore
from lumen.daemon.connectors.email_menu import EmailStore, GmailSync
from lumen.daemon.connectors.mail_rules import RuleStore
from lumen.daemon.connectors.gcal import (CalendarMarkerWriter, CalendarSync,
                                          EventStore)
from lumen.daemon.connectors.manabi import ManabiStatus
from lumen.daemon.connectors.memory_log import MemoryLog
from lumen.daemon.connectors.notes import NotesStore
from lumen.daemon.connectors.procedures import ProcedureStore
from lumen.daemon.connectors.suggestions import SuggestionStore
from lumen.daemon.connectors.todos import TodoStore
from lumen.daemon.ipc_server import IPCServer
from lumen.daemon.llm.backend import LLMBackend
from lumen.daemon.llm.client import OllamaClient
from lumen.daemon.llm.claude_cli import ClaudeCliClient
from lumen.daemon.llm.mcp_bridge import LazyBridge
from lumen.daemon.llm.model_router import ModelRouter
from lumen.daemon.llm.tool_log import ToolLog
from lumen.daemon.memory_worker import MemoryWorker
from lumen.daemon.router import Router
from lumen.daemon.write_gate import GrantStore, WriteGate, write_tools_map

log = logging.getLogger("lumen.daemon")


async def run() -> None:
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    ollama = OllamaClient(cfg.ollama_url, cfg.model, cfg.keep_alive, think=cfg.think)
    claude_cfg = cfg.claude
    claude_models = dict(claude_cfg.models)
    default_claude_id = claude_models.get(claude_cfg.default_model,
                                          "claude-haiku-4-5-20251001")
    claude = ClaudeCliClient(
        model=default_claude_id,
        cli_path=claude_cfg.cli_path,
        timeout_seconds=claude_cfg.timeout_seconds,
        max_concurrent=claude_cfg.max_concurrent,
    )
    llm = LLMBackend(ollama, claude, conn,
                     claude_models=claude_models,
                     background_pause_at=claude_cfg.background_pause_at)
    bridge = LazyBridge(list(cfg.mcp.servers)) if cfg.mcp.enabled else None
    model_router = ModelRouter(cfg.model, cfg.escalation_model)
    tool_log = ToolLog(cfg.mcp.log_path) if cfg.mcp.enabled else None
    events = EventStore(conn)
    calendar = CalendarSync(events, cfg.google, cfg.sync)
    emails = EmailStore(conn)
    rules = RuleStore(conn)
    mail = GmailSync(emails, cfg.google, cfg.sync, rules=rules)
    # One writer, shared by the poll loop and the router's user-initiated routes.
    markers = CalendarMarkerWriter(cfg.google)
    canvas_prefs = CanvasPrefs(conn)
    canvas_queue = CanvasQueue(conn)
    canvas_alerts = CanvasAlerts(conn)
    canvas_proposals = CanvasProposals(conn)
    canvas = CanvasSync(CanvasStore(conn), cfg.canvas,
                        todos=TodoStore(conn), llm=llm,
                        session_path=cfg.canvas.session_path,
                        markers=markers, prefs=canvas_prefs,
                        queue=canvas_queue, alerts=canvas_alerts,
                        proposals=canvas_proposals, events=events,
                        # #59: re-read the window after Canvas writes to it, or
                        # the new assignments sit on Google unseen by the UI.
                        calendar=calendar)
    # Before any task starts, so `connected` is already true for the first
    # status query a freshly-launched UI makes.
    canvas.restore_session()
    # #38: persistent per-connection Disable. The poll loops read this each tick
    # via `paused`, so a Settings toggle pauses/resumes sync with no restart.
    connections = ConnectionState(conn)
    mail.paused = lambda: not connections.enabled("gmail")
    calendar.paused = lambda: not connections.enabled("google_calendar")
    canvas.paused = lambda: not connections.enabled("canvas")
    memory_log = MemoryLog(conn)
    procedures = ProcedureStore(cfg.procedures_dir, cfg.memory, llm)
    memory_worker = MemoryWorker(llm, memory_log, cfg.memory_path, cfg.memory,
                                 procedures=procedures)
    # The Settings model switch, read live on the same per-tick basis: with the
    # model off, neither background pass may pull it into RAM. Routes are gated
    # separately inside Router (it holds `connections` already).
    canvas.model_paused = llm.model_paused
    memory_worker.model_paused = llm.model_paused
    broker = ConfirmBroker()   # shared: router resolves, the write gate awaits
    write_gate = (WriteGate(GrantStore(cfg.mcp.grants_path), broker,
                            write_tools_map(cfg.mcp.servers))
                  if cfg.mcp.enabled else None)
    router = Router(llm, TodoStore(conn), BookStore(conn), calendar=calendar,
                    mail=mail, mail_store=emails, canvas=canvas,
                    bridge=bridge, confirm=broker, write_gate=write_gate,
                    marker_writer=markers, canvas_prefs=canvas_prefs,
                    canvas_queue=canvas_queue, canvas_alerts=canvas_alerts,
                    canvas_proposals=canvas_proposals,
                    model_router=model_router, tool_log=tool_log,
                    conversations=ConversationStore(conn),
                    suggestions=SuggestionStore(conn),
                    scheduling=cfg.scheduling,
                    notes=NotesStore(
                        conn,
                        lambda texts: llm.embed(texts, cfg.notes.embed_model),
                        cfg.notes.folder, cfg.notes.embed_model),
                    write_dir=cfg.notes.write_dir or cfg.notes.folder,
                    manabi=ManabiStatus(cfg.manabi.db_path),
                    memory=memory_log, memory_path=cfg.memory_path,
                    memory_cap=cfg.memory.blob_cap_chars,
                    procedures=procedures,
                    distill_trigger=memory_worker.schedule,
                    config=cfg, rules=rules, connection_state=connections,
                    max_iterations=cfg.mcp.max_iterations)
    server = IPCServer(cfg.socket_path, router)
    await server.start()
    poll_task = asyncio.create_task(calendar.poll_forever())
    mail_task = asyncio.create_task(mail.poll_forever())
    canvas_task = (asyncio.create_task(canvas.poll_forever())
                   if cfg.canvas.enabled else None)
    log.info("listening on %s (model=%s, keep_alive=%s, db=%s)",
             cfg.socket_path, cfg.model, cfg.keep_alive, cfg.db_path)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("shutting down")
    bg_tasks = [poll_task, mail_task]
    if canvas_task is not None:
        bg_tasks.append(canvas_task)
    for t in bg_tasks:
        t.cancel()
    await asyncio.gather(*bg_tasks, return_exceptions=True)
    await server.stop()
    # Detached writes (an open-as-read receipt, say) get a short grace period to
    # land rather than being dropped on the floor mid-shutdown.
    try:
        await asyncio.wait_for(router.drain_background(), timeout=5)
    except asyncio.TimeoutError:
        log.warning("background writes still pending at shutdown — dropping")
    await memory_worker.aclose()
    await llm.aclose()
    if bridge is not None:
        await bridge.aclose()
    conn.close()


def stop() -> int:
    """`lumen-daemon --stop` (#47). SIGTERM every daemon this user owns — the
    clean-shutdown path, so pollers stop and in-flight writes get their grace
    period. Leaves the UI alone; `lumen --quit` is the take-everything-down
    switch."""
    from lumen import instance_lock
    pids = instance_lock.daemon_pids()
    if not pids:
        print("lumen-daemon: nothing running")
        return 0
    instance_lock.terminate(pids)
    print(f"lumen-daemon: stopped {len(pids)} daemon(s)")
    return 0


def main() -> None:
    if "--stop" in sys.argv or "--kill" in sys.argv:
        raise SystemExit(stop())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
