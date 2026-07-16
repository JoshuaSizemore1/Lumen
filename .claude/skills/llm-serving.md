# Skill: LLM Serving

## Runtime
Ollama, run as a systemd user service. It's always listening on localhost, but does NOT load a model into RAM until the first request hits it — that's the behavior we rely on for the power constraint.

## Idle-unload
Set `OLLAMA_KEEP_ALIVE` (env var, or per-request `keep_alive` param) to something short, e.g. `10m`. After that many minutes with no requests, Ollama evicts the model from memory automatically. Do not set this to `-1` (infinite) — that defeats the whole point.

Example systemd user unit override:
```ini
# ~/.config/systemd/user/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_KEEP_ALIVE=10m"
```

## Model choice (slots decided 2026-07-07)
| Slot | Model | When it enters |
|---|---|---|
| Fast path / router / tool calls | `qwen3:4b-instruct` (Q4, ~2.5GB, non-thinking variant) | Phase 1 default |
| Tool-chain escalation | `qwen3:14b` (Q4, 9.3GB) — pulled + benchmarked 2026-07-10, wired but OFF by default (see below) | Phase 3+, when MCP chains need it (see `mcp-integration.md`) |
| Writing/synthesis escalation (candidate) | `gemma3:12b-it-qat` (~8GB) | Phase 7–8 benchmark — stronger prose + Japanese |
| Deep-reasoning, no-tools (discussed, not committed) | thinking-mode model, e.g. `qwen3:4b` with `think: true` | Not scheduled — see `project-scope.md` "Discussed but not yet committed" for why, and the open questions before this could be built |

- The fast-path slot must be a model with native tool-call support in Ollama's `tools` API (Qwen3 qualifies; Gemma 3 does not — never route tool calls to Gemma, prose-only).
- Escalation is the exception, not the default — every escalation is a bigger RAM/thermal hit. Model names live in `config.toml`; swapping a slot is config, not code.
- Thermal ceiling on this hardware (Core Ultra 9 285H, 32GB shared RAM, iGPU only): ~14B-class for regular use. 27B+ fits in RAM but not the thermal envelope.
- Benchmark cold-start latency on the actual hardware before committing to a model size. If it's not sub-2-second on a cold load, the "quick-launcher" UX will feel broken.
- Thinking mode off on the fast path (`think = false`, benchmarked 2026-07-07: ~2900 hidden tokens ≈ 8min CPU per trivial query with it on). Revisit per-slot if an escalation task genuinely benefits.
- Follow-up (2026-07-07): `think = false` alone is not sufficient on this machine's Ollama build (0.31.1). For the thinking-capable `qwen3:4b`, it only disables Ollama's parser — reasoning still generates and leaks into visible content as literal `<think>...</think>` text (one-sentence answers ~57s total, a two-word prompt >3min). Fixed by switching the fast-path slot to the non-thinking `qwen3:4b-instruct` variant, which reports no `thinking` capability (`/api/tags`) and never generates reasoning tokens. `think = false` stays in config — correct and needed for any future thinking-capable slot, just not sufficient by itself on a thinking model.
- Phase 3 live verification (2026-07-09): `qwen3:4b-instruct` correctly single-tool-called both real MCP servers over the actual daemon path — filesystem (`list_allowed_directories` then `list_directory`, once the allowlist included the discovery tool) and Open Library (`search_books`) — and both final answers were grounded in the tool result (see `mcp-integration.md`, `tool-calls.jsonl`). 14B escalation is still deferred — no chained/multi-tool scenario has been exercised yet; the `ModelRouter` seam (`daemon/llm/model_router.py`) is in place and always returns the fast model for now. MCP server subprocesses (`npx`/`node`, `python -m lumen.mcp_servers.openlibrary`) carry no model of their own and don't touch Ollama's idle-unload — confirmed they're separate OS processes from Ollama and exit independently of it.

- **14B escalation benchmark (2026-07-10, Phase 4.5, `scripts/bench_escalation.py`)**: `qwen3:14b` Q4 on this hardware — cold start (unloaded → first token) 11.2s, warm 0.9s, 3/3 well-formed `write_file` tool calls, thermals 52→55°C (no alarm). Isolated per-turn generation 20–40s. **But the real daemon flow measured 252s to the first tool call** (live smoke over the socket): with all ~13 MCP tool schemas in the prompt, CPU-bound prompt eval dominates, vs ~20s per exchange for `qwen3:4b-instruct` on the identical task. Verdict: escalation stays **unset** in config (`[llm] escalation_model`) — the ModelRouter branch, `FS_WRITE_HINT`, and this benchmark script are all in place, so enabling it is one config line if a future task (Phase 8 multi-tool chains, the tier's real motivation) proves the 4B actually failing. The write-confirm gate, not model size, is the safety mechanism for fs writes.

- **Embedding slot (decided 2026-07-14, Phase 8 notes Q&A)**: `nomic-embed-text` (274MB, dim 768). Benchmarked on this hardware against `embeddinggemma` (621MB, dim 768): cold load+first embed 0.45s vs 1.80s, warm single query 0.09s vs 0.30s, 20-chunk batch 2.9s vs 3.3s — nomic wins on every axis and is a quarter of the RAM. Config `[notes] embed_model`; `keep_alive` rides on every `/api/embed` call so it idle-unloads like the chat models. Changing the model wipes and rebuilds the notes index (vectors from different models don't share a space — `connectors/notes.py` keys the index by model name).

## Router behavior
`daemon/llm/` should expose a single entrypoint that:
1. Picks fast-path vs escalation model based on request classification (keep this classification cheap — a regex/heuristic pass is fine before reaching for the LLM itself to decide).
2. Sends `keep_alive` explicitly on every request rather than relying only on the service-level default, so behavior is consistent even if the systemd override gets lost.
3. Never blocks the UI on a cold model load without showing a loading state — first request after idle-unload will be slower.

## Power/thermal validation (Phase 11, benchmarked 2026-07-15 on the real hardware)
Zenbook Duo, Core Ultra 9 285H, 32GB shared, **iGPU-only — Ollama runs the model 100% on CPU** (`ollama ps` shows `100% CPU`, no GPU offload). All numbers over the real daemon socket against real Ollama; harness in the session scratchpad. Sensors: `sensors -j` → `coretemp-isa-0000/Package id 0/temp1_input` (temp), `asus-isa-000a` cpu_fan/gpu_fan (RPM).

- **Cold-start (idle-unloaded → first token), `qwen3:4b-instruct` via the daemon: ~11s.** Ollama's own metrics decompose it: **3.3s model load + 7.8s prompt-eval + 0.3s generation**. Prompt-eval dominates and is CPU-bound at **~24 tok/s** — it re-evaluates the ~175-token `router.IDENTITY` system prompt on every cold request. (Pure Ollama cold with a trivial 15-token prompt is only 3.9s; the gap is entirely IDENTITY prompt-eval. An early "raw 4.6s" comparison was a red herring — it used a stub system prompt, not the real IDENTITY.)
  - **This misses the skill's aspirational sub-2s cold-load target.** The mitigating reality: during an active session the 10-min idle-unload keeps the model resident, so nearly every query is **warm** (below). The 11s hit lands only on the *first* query after ≥10 min idle.
  - **The biggest lever is already benchmarked and un-pulled: the iGPU/Vulkan backend.** Every Phase 11 number here is `100% CPU` (Ollama shows no GPU offload). The 2026-07-11 iGPU benchmark measured **prompt-eval 22→231 tok/s (~10×)** with `ollama-vulkan` — that would cut the 7.8s IDENTITY prompt-eval to ~0.8s and drop cold-start to ~4s (and warm stays sub-second). It needs one root action (`sudo pacman -S ollama-vulkan && systemctl --user restart ollama`; the `OLLAMA_IGPU_ENABLE=1` drop-in is already in place). This is the highest-value cold-start fix and it's on Josh's to-do list, not a code change.
- **Warm (model resident, IDENTITY prefix KV-cached by Ollama): ~0.6–0.9s first token.** Once one real generation has run, the stable IDENTITY prefix is cached, so prompt-eval ≈ 0 on follow-ups. This is the common-case latency and it's good.
- **`warm()` (launcher summon preload) does NOT hide the cold hit.** It POSTs empty `messages`, so it loads the *weights* (~3.3s) but never primes/caches the IDENTITY prefix. The first real query after warm still pays the full 7.8s prompt-eval (measured 8.2s even with a 5s typing gap); only the *second* query is fast (0.86s). **Open follow-up (user decision pending):** either (a) make `warm()` send the real IDENTITY prefix (or a 1-token gen with it) so Ollama caches it, (b) trim the 175-token IDENTITY to cut prompt-eval — but it encodes hard-won anti-fabrication behavior (see dev-plan bugs 2/3), or (c) accept it since active-session queries are warm.
- **Idle behavior:** daemon idle CPU **0.00%** over a 12s between-ticks window (avg 0.1% over ~12 min; a Gmail poll tick measured 0.171s of loop time — brief, not sustained). Idle-unload verified live: `keep_alive` rides every request, `ollama ps` shows a finite `UNTIL` countdown, and after the daemon was killed the model stayed resident under Ollama's own 10-min timer and evicted naturally at the mark (RAM returned to baseline).
- **MCP subprocesses** spawn only on first tool use (`LazyBridge`): 4 servers (fs via `npx`, openlibrary, gcal, mail), all children of the daemon PID, and **all 4 exited when the daemon was killed** — none orphaned.
- **Sustained mixed session** (~6.4 min, briefing + a dozen chats + tool calls, model resident throughout): peak package temp **65°C** (idle baseline 44–47°C; 0 samples ≥70°C); fans ramp from **0 RPM idle to ~4000–4100 RPM** under load (audible but far from max); RAM **+4.2 GB** under load (3.9 GB model + KV cache at num_ctx 8192 + MCP subprocs). Thermals sit comfortably inside the envelope (crit 105°C) — no throttle, no thermal alarm.
- **Verdict:** thermals and idle behavior pass cleanly. The one real gap is interactive **cold-start (~11s)**, which is inherent to CPU-only prompt-eval of the identity prompt under the non-negotiable idle-unload — not a thermal problem. Warm latency (~0.7s) is the everyday experience.

## What NOT to do
- Don't run a second model server "just in case" — one Ollama instance, one idle-unload policy.
- Don't disable idle-unload to shave latency — the point of this app is it's always available *and* doesn't cook the laptop.
- Don't "fix" cold-start by trimming the IDENTITY prompt without weighing it against the anti-fabrication behavior it encodes — that's a product/behavior call, not a free optimization (Phase 11 finding).
