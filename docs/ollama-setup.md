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
ollama pull qwen3:4b-instruct       # ~2.5GB download
curl -s localhost:11434/api/tags | python3 -m json.tool   # should list qwen3:4b-instruct
```

Model slots (decided 2026-07-07, see `.claude/skills/llm-serving.md`):

| Slot | Model | Status |
|---|---|---|
| Fast path / router / tools | `qwen3:4b-instruct` | pull now |
| Tool-chain escalation | Qwen3 14B-class | Phase 3 — do NOT pull yet |
| Writing escalation candidate | `gemma3:12b-it-qat` | Phase 7 benchmark — do NOT pull yet |

> **Thinking mode:** qwen3 models "think" by default — thousands of hidden
> reasoning tokens per query, which at CPU speeds means minutes of latency and
> heat before the first visible word (measured: 8m16s for a two-word answer).
> Ollama's `think=false` request parameter is supposed to suppress this, but on
> this machine's Ollama build (0.31.1) it only disables the *parser* — reasoning
> still generates and streams into visible content as literal `<think>...</think>`
> text (measured: one-sentence answers ~57s total; a two-word prompt exceeded
> 3 minutes). The fix that actually works here: the fast-path slot uses the
> non-thinking `qwen3:4b-instruct` variant, which has no `thinking` capability
> at all and never generates reasoning tokens. `think = false` stays set in
> `config.toml` regardless — it's correct (and needed) for any future
> thinking-capable escalation slot, just harmless as a no-op on this one.

## 4. Start / stop / call — the control surface

| Action | How |
|---|---|
| Start the server | `systemctl --user start ollama` (auto-starts on login once enabled) |
| Stop the server | `systemctl --user stop ollama` |
| Load the model | automatic, on the first request (cold start = a few seconds) |
| Call the model | only ever through the Lumen daemon (`daemon/llm/client.py`) |
| Unload NOW ("sleep") | tray menu → "Sleep model now", or: `curl localhost:11434/api/chat -d '{"model":"qwen3:4b-instruct","messages":[],"keep_alive":0}'` |
| What's loaded? | `ollama ps` |

## 5. Verify idle-unload actually works (Phase 1 success criterion)

```bash
ollama run qwen3:4b-instruct "say hi"   # loads the model
ollama ps                               # shows qwen3:4b-instruct resident, with an UNTIL column
```

For a fast check, restart the user service with a 1-minute override, ask once,
and watch it evict:

```bash
systemctl --user edit ollama   # drop-in: [Service] Environment="OLLAMA_KEEP_ALIVE=1m"
systemctl --user restart ollama
ollama run qwen3:4b-instruct "say hi" && sleep 75 && ollama ps   # expect: empty table
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
