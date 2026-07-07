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
