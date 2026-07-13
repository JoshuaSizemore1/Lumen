---
name: verify
description: How to live-verify Lumen changes — drive the daemon over its unix socket and screenshot the PyQt UI offscreen
---

# Verifying Lumen changes

## Daemon surface (most changes)

The daemon's newline-delimited-JSON unix socket is the programmatic surface —
the UI is a thin client over it.

- Socket: `$XDG_RUNTIME_DIR/lumen/daemon.sock` (`/run/user/1000/lumen/daemon.sock`)
- Check what's running: `pgrep -af lumen-daemon` — a running daemon has OLD
  code; kill it and restart to pick up changes:
  `nohup uv run lumen-daemon > /path/to/scratch/daemon.log 2>&1 &`
- Drive it with a tiny asyncio client: connect, write one
  `{"id": N, "type": "...", "payload": {...}}\n` line, read reply lines.
  **Gotcha:** pass `limit=16*1024*1024` to `open_unix_connection` — a full
  `emails.list`/`mail.refresh` response overflows the 64 KB readline default.
- Streaming types (`chat`) emit multiple lines (`conversation_id`, `chunk`,
  `compose_request`/`confirm_request`, `done`). Answer a confirm/compose on a
  **second connection** (`confirm.response` / `compose.response`) — the first
  is blocked awaiting it.
- LLM-touching requests load the model (qwen3:4b-instruct via Ollama); first
  token can take ~30–120 s cold. Use generous timeouts.

## UI surface

Offscreen render, no compositor (never map real windows — the desktop session
exports `QT_QPA_PLATFORM=wayland;xcb`):

```bash
QT_QPA_PLATFORM=offscreen uv run python scripts/screenshot.py \
    lumen.ui_v2.main:build_window shots/ --size 1280x800
```

For one specific state, build in a heredoc: `build_window()`, drive
`win.state` signals / `win.switch_to("mail")`, `app.processEvents()`, then
`win.grab().save(path)`.

## Gmail-touching flows

Sends are real. Verify with a **self-send** to the user's own address
(joshjsizemore@gmail.com), subject clearly marked as a test. Close the loop
via the mirror: `mail.refresh`, then `emails.search` for the subject — the
sent message coming back with `SENT` (+`INBOX` for self-sends) labels proves
Gmail accepted it. No other recipients, ever.