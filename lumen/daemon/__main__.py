"""Daemon entrypoint: config -> db -> Ollama client -> router -> IPC server,
until SIGINT/SIGTERM."""

import asyncio
import logging
import signal

from lumen.daemon import db
from lumen.daemon.config import load_config
from lumen.daemon.confirm import ConfirmBroker
from lumen.daemon.connectors.books import BookStore
from lumen.daemon.connectors.conversations import ConversationStore
from lumen.daemon.connectors.email_menu import EmailStore, GmailSync
from lumen.daemon.connectors.mail_rules import RuleStore
from lumen.daemon.connectors.gcal import CalendarSync, EventStore
from lumen.daemon.connectors.manabi import ManabiStatus
from lumen.daemon.connectors.memory_log import MemoryLog
from lumen.daemon.connectors.notes import NotesStore
from lumen.daemon.connectors.procedures import ProcedureStore
from lumen.daemon.connectors.suggestions import SuggestionStore
from lumen.daemon.connectors.todos import TodoStore
from lumen.daemon.ipc_server import IPCServer
from lumen.daemon.llm.client import OllamaClient
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
    llm = OllamaClient(cfg.ollama_url, cfg.model, cfg.keep_alive, think=cfg.think)
    bridge = LazyBridge(list(cfg.mcp.servers)) if cfg.mcp.enabled else None
    model_router = ModelRouter(cfg.model, cfg.escalation_model)
    tool_log = ToolLog(cfg.mcp.log_path) if cfg.mcp.enabled else None
    events = EventStore(conn)
    calendar = CalendarSync(events, cfg.google, cfg.sync)
    emails = EmailStore(conn)
    rules = RuleStore(conn)
    mail = GmailSync(emails, cfg.google, cfg.sync, rules=rules)
    memory_log = MemoryLog(conn)
    procedures = ProcedureStore(cfg.procedures_dir, cfg.memory, llm)
    memory_worker = MemoryWorker(llm, memory_log, cfg.memory_path, cfg.memory,
                                 procedures=procedures)
    broker = ConfirmBroker()   # shared: router resolves, the write gate awaits
    write_gate = (WriteGate(GrantStore(cfg.mcp.grants_path), broker,
                            write_tools_map(cfg.mcp.servers))
                  if cfg.mcp.enabled else None)
    router = Router(llm, TodoStore(conn), BookStore(conn), calendar=calendar,
                    mail=mail, mail_store=emails,
                    bridge=bridge, confirm=broker, write_gate=write_gate,
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
                    config=cfg, rules=rules,
                    max_iterations=cfg.mcp.max_iterations)
    server = IPCServer(cfg.socket_path, router)
    await server.start()
    poll_task = asyncio.create_task(calendar.poll_forever())
    mail_task = asyncio.create_task(mail.poll_forever())
    log.info("listening on %s (model=%s, keep_alive=%s, db=%s)",
             cfg.socket_path, cfg.model, cfg.keep_alive, cfg.db_path)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("shutting down")
    poll_task.cancel()
    mail_task.cancel()
    await asyncio.gather(poll_task, mail_task, return_exceptions=True)
    await server.stop()
    await memory_worker.aclose()
    await llm.aclose()
    if bridge is not None:
        await bridge.aclose()
    conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
