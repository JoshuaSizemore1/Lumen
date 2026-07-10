"""Daemon entrypoint: config -> db -> Ollama client -> router -> IPC server,
until SIGINT/SIGTERM."""

import asyncio
import logging
import signal

from lumen.daemon import db
from lumen.daemon.config import load_config
from lumen.daemon.connectors.books import BookStore
from lumen.daemon.connectors.todos import TodoStore
from lumen.daemon.ipc_server import IPCServer
from lumen.daemon.llm.client import OllamaClient
from lumen.daemon.llm.mcp_bridge import LazyBridge
from lumen.daemon.llm.model_router import ModelRouter
from lumen.daemon.llm.tool_log import ToolLog
from lumen.daemon.router import Router

log = logging.getLogger("lumen.daemon")


async def run() -> None:
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    llm = OllamaClient(cfg.ollama_url, cfg.model, cfg.keep_alive, think=cfg.think)
    bridge = LazyBridge(list(cfg.mcp.servers)) if cfg.mcp.enabled else None
    model_router = ModelRouter(cfg.model)
    tool_log = ToolLog(cfg.mcp.log_path) if cfg.mcp.enabled else None
    router = Router(llm, TodoStore(conn), BookStore(conn), bridge=bridge,
                    model_router=model_router, tool_log=tool_log,
                    max_iterations=cfg.mcp.max_iterations)
    server = IPCServer(cfg.socket_path, router)
    await server.start()
    log.info("listening on %s (model=%s, keep_alive=%s, db=%s)",
             cfg.socket_path, cfg.model, cfg.keep_alive, cfg.db_path)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("shutting down")
    await server.stop()
    await llm.aclose()
    if bridge is not None:
        await bridge.aclose()
    conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
