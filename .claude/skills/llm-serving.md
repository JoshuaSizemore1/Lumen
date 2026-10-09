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

- **14B re-benchmark under Vulkan (2026-07-17, full-scope-audit follow-up)**: the iGPU backend transforms the speed story but adds a blocker. Same script: cold 13.5s, warm first token 0.6s, 3/3 tool calls at **7.3–8.3s/turn** (vs 20–40s CPU), thermals 49→54°C, `ollama ps` 100% GPU at the daemon's `num_ctx=8192`. **Blocker: a ~5.6k-token prompt — the size of a cold tool-loop prefix (~13 schemas + identity + grounding) — reproducibly crashed the Vulkan runner** (`vk::Queue::submit: ErrorDeviceLost`, HTTP 500, ollama-vulkan 0.32.1; hit on two separate attempts, runner restarts on the next request but the in-flight request is lost). The 4B is unaffected: the identical 5.6k prompt evaluated fine at **117 tok/s** (prompt-eval rate is length-dependent — 248 tok/s at 1.2k tokens; the daily driver is healthy). Verdict: escalation stays **unset** — no longer for latency (7–8s/turn would be acceptable) but because the runner dies on exactly the workload the slot exists for. Re-test on the next ollama-vulkan release; the enable remains one config line.

- **Embedding slot (decided 2026-07-14, Phase 8 notes Q&A)**: `nomic-embed-text` (274MB, dim 768). Benchmarked on this hardware against `embeddinggemma` (621MB, dim 768): cold load+first embed 0.45s vs 1.80s, warm single query 0.09s vs 0.30s, 20-chunk batch 2.9s vs 3.3s — nomic wins on every axis and is a quarter of the RAM. Config `[notes] embed_model`; `keep_alive` rides on every `/api/embed` call so it idle-unloads like the chat models. Changing the model wipes and rebuilds the notes index (vectors from different models don't share a space — `connectors/notes.py` keys the index by model name).

## Router behavior
`daemon/llm/` should expose a single entrypoint that:
1. Picks fast-path vs escalation model based on request classification (keep this classification cheap — a regex/heuristic pass is fine before reaching for the LLM itself to decide).
2. Sends `keep_alive` explicitly on every request rather than relying only on the service-level default, so behavior is consistent even if the systemd override gets lost.
3. Never blocks the UI on a cold model load without showing a loading state — first request after idle-unload will be slower.

## Power/thermal validation (Phase 11, benchmarked 2026-07-15 on the real hardware)
Zenbook Duo, Core Ultra 9 285H, 32GB shared, **iGPU-only — Ollama runs the model 100% on CPU** (`ollama ps` shows `100% CPU`, no GPU offload). *(Historical baseline — the Vulkan/iGPU backend went live 2026-07-17, see the lever bullet below.)* All numbers over the real daemon socket against real Ollama; harness in the session scratchpad. Sensors: `sensors -j` → `coretemp-isa-0000/Package id 0/temp1_input` (temp), `asus-isa-000a` cpu_fan/gpu_fan (RPM).

- **Cold-start (idle-unloaded → first token), `qwen3:4b-instruct` via the daemon: ~11s.** Ollama's own metrics decompose it: **3.3s model load + 7.8s prompt-eval + 0.3s generation**. Prompt-eval dominates and is CPU-bound at **~24 tok/s** — it re-evaluates the ~175-token `router.IDENTITY` system prompt on every cold request. (Pure Ollama cold with a trivial 15-token prompt is only 3.9s; the gap is entirely IDENTITY prompt-eval. An early "raw 4.6s" comparison was a red herring — it used a stub system prompt, not the real IDENTITY.)
  - **This misses the skill's aspirational sub-2s cold-load target.** The mitigating reality: during an active session the 10-min idle-unload keeps the model resident, so nearly every query is **warm** (below). The 11s hit lands only on the *first* query after ≥10 min idle.
  - **The biggest lever — the iGPU/Vulkan backend — was pulled 2026-07-17 and verified live.** `ollama-vulkan` 0.32.1 installed (pacman; the initial 404 was just a stale package DB, fixed with `-Syu`), user service restarted. Ollama now detects the Arc iGPU (`library=Vulkan`, `type=iGPU`, ~20 GiB shared VRAM available) and `ollama ps` shows `qwen3:4b-instruct` at **100% GPU**. Measured prompt-eval **248 tok/s** on a 1199-token prompt (vs ~22–24 tok/s CPU, ~11×) — the ~175-token IDENTITY eval drops from ~7.8s to well under 1s, putting cold-start around the predicted ~4s (dominated by model load, 8.5s on the very first Vulkan load, cheaper after). The pre-staged `OLLAMA_IGPU_ENABLE=1` drop-in (`~/.config/systemd/user/ollama.service.d/igpu.conf`) was the gate that mattered; `OLLAMA_VULKAN` defaults on in this build once the Vulkan runner is present. One behavior change to know: with no explicit `num_ctx`, this build picks a VRAM-based default context (32768 here → 7.5 GB shared-RAM footprint); Lumen's own requests are unaffected when they send `num_ctx`. Phase 11 numbers above remain the CPU-only baseline — re-run the daemon-path cold-start benchmark when it next matters.
- **Warm (model resident, IDENTITY prefix KV-cached by Ollama): ~0.6–0.9s first token.** Once one real generation has run, the stable IDENTITY prefix is cached, so prompt-eval ≈ 0 on follow-ups. This is the common-case latency and it's good.
- **`warm()` (launcher summon preload) now primes the prefix — option (a), shipped.** It no longer POSTs empty `messages`; `Router._warm_prefix()` hands it the stable `IDENTITY + memory` system block plus a trivial user turn, and `client.warm()` runs a `num_predict=1` generation over it (same `num_ctx`, so no reload). That forces the CPU-bound prompt-eval of the identity prefix *into the summon window*, so Ollama caches its KV and the first real query reuses it instead of paying the ~7.8s eval. Per-query context (todo/calendar/fs/mail) is keyword-gated and varies, so it is deliberately not primed — only the constant head is worth caching. Remaining levers if cold-start still matters: (b) trim the ~175-token IDENTITY to cut the primed eval — but it encodes hard-won anti-fabrication behavior (see dev-plan bugs 2/3), or the big one, the iGPU/Vulkan backend above. Note the prime warms the **fast model only**; an escalation-model first query would still be cold (escalation is unset, so moot today).
- **Idle behavior:** daemon idle CPU **0.00%** over a 12s between-ticks window (avg 0.1% over ~12 min; a Gmail poll tick measured 0.171s of loop time — brief, not sustained). Idle-unload verified live: `keep_alive` rides every request, `ollama ps` shows a finite `UNTIL` countdown, and after the daemon was killed the model stayed resident under Ollama's own 10-min timer and evicted naturally at the mark (RAM returned to baseline).
- **MCP subprocesses** spawn only on first tool use (`LazyBridge`): 4 servers (fs via `npx`, openlibrary, gcal, mail), all children of the daemon PID, and **all 4 exited when the daemon was killed** — none orphaned.
- **Sustained mixed session** (~6.4 min, briefing + a dozen chats + tool calls, model resident throughout): peak package temp **65°C** (idle baseline 44–47°C; 0 samples ≥70°C); fans ramp from **0 RPM idle to ~4000–4100 RPM** under load (audible but far from max); RAM **+4.2 GB** under load (3.9 GB model + KV cache at num_ctx 8192 + MCP subprocs). Thermals sit comfortably inside the envelope (crit 105°C) — no throttle, no thermal alarm.
- **Verdict:** thermals and idle behavior pass cleanly. The one real gap is interactive **cold-start (~11s)**, which is inherent to CPU-only prompt-eval of the identity prompt under the non-negotiable idle-unload — not a thermal problem. Warm latency (~0.7s) is the everyday experience.

## llama.cpp direct: evaluated 2026-07-18, reverted 2026-07-19 — don't re-derive this
A `llm.backend = "llamacpp"` path (daemon-supervised `llama-server`, OpenAI-compatible client,
`--sleep-idle-seconds` for idle-unload) was built, measured, and then deleted. Recording it here
so the next benchmark that shows llama.cpp "beating" Ollama doesn't restart the cycle.

- **Ollama *is* llama.cpp** — it ships `/usr/lib/ollama/llama-server` and runs the model in it.
  A "llama.cpp vs Ollama" benchmark is not two engines; it is one engine with and without a
  wrapper. Treat any framing of it as a migration between runtimes as a category error.
- **The measured delta is wrapper overhead: a fixed ~0.5–0.7 s per request**, not a multiplier —
  1.53× at 2 tool schemas, 1.29× at 5, 1.23× at 16 (real production prompt, both arms warmed,
  alternating reps). The ratio *shrinks* as the request gets heavier. An earlier "2.03×" came
  from timing a 13-schema surface production never sends.
- **The cost is structural, not the line count**: a subprocess supervisor on the critical path, a
  *second* idle-unload mechanism to keep correct alongside `keep_alive` (two ways to violate the
  non-negotiable constraint), embeddings permanently split across two servers (llama-server holds
  one model per process), and a hardcoded GGUF blob path that rots on the next `ollama pull`.
- **Verdict: not worth it, and it violated the rule directly below.** If per-request latency
  matters later, attack Ollama's own overhead — don't add a process. Full workings in
  `docs/research/local-inference-eval.md` and the 2026-07-18 tool-accuracy spec.

## Claude mode via the `claude` CLI (built 2026-09-24)
Opt-in third mode (Settings → model: **Off / Local / Claude**). Spec + measurements:
`docs/superpowers/specs/2026-09-24-claude-backend-design.md`. It runs on Josh's own subscription
(Pro), not an API key. There is no second model server: each request spawns one short-lived
`claude -p` process.

- **Seam:** `daemon/llm/backend.py` `LLMBackend` wraps `OllamaClient` + `claude_cli.ClaudeCliClient`
  and is handed to everything that used to get the `OllamaClient`, so feature modules are backend-agnostic.
  Mode and the Haiku/Sonnet choice are stored in the SQLite `llm_state` table. `embed` **always** goes to Ollama
  (notes search keeps `nomic-embed-text`). Entering Claude or Off unloads the Ollama chat model at once.
- **Invocation is locked down:** `--tools "" --setting-sources "" --strict-mcp-config
  --disable-slash-commands --no-session-persistence`, thinking off via `--settings
  '{"alwaysThinkingEnabled":false}'`, cwd `~/.local/state/lumen/claude-cwd` (0700).
  None of Josh's Claude Code hooks, plugins, CLAUDE.md or MCP servers apply.
- **Privacy:** the user turn goes on stdin, and the **system prompt goes via `--system-prompt-file`**
  (a 0600 temp file, deleted after the call). Lumen's system message carries mail/calendar/todo
  context and the memory blob, and argv is readable in `ps`.
- **Tool loop stays in the daemon.** Each iteration is one CLI call with `--json-schema`
  `{tool_calls:[{name(enum), arguments}], answer}`, and the tool list is exactly the router's. This keeps the
  confirm gates and the tool log unchanged. Haiku sometimes calls an offered tool *natively*, the CLI refuses
  it, and then Haiku says the tool "isn't available". `_run_json_call` harvests those native calls as the
  real request and stops the run at the refusal. It also repeats the framing at the end of the prompt.
- **Plain `chat()`** appends a one-line "never write tool-call markup" note. Without it, Haiku typed fake
  `<function_calls>` XML when routed to plain chat. Keep the note one line: a chattier note talked Haiku
  into prose on strict-format calls (intent labels, triage).
- **Usage:** every run emits `rate_limit_event` (5h/7d utilization), which is cached for Settings and for the
  background backoff (canvas/memory worker pause at ≥ `background_pause_at`, default 0.80).
  **`status: "allowed_warning"` still answers** (seen at 90%). Only non-`allowed*` statuses are a limit.
  Background tasks set the `BACKGROUND` contextvar and yield the 2-slot semaphore to interactive calls.
- **Measured live (Haiku 4.5):** plain answer 1.6–4 s. One-lookup questions 6–7 s (email, files). The book
  lookup took 30 s (2 lookups). Briefing 3.4 s. Triage of 20 mails took 44 s. A calendar event went through
  the confirm dialog, which still gated it. Eval: 17/19 tool-selection cases. Both misses were answered
  from injected mail context instead of re-searching, which is acceptable. `ollama ps` was empty throughout.
  Eval command: `LUMEN_EVAL_LIVE=1 LUMEN_EVAL_BACKEND=claude [LUMEN_EVAL_MODEL=sonnet] uv run pytest tests/eval/test_live_accuracy.py`.
- **Failures never fall back to local** (product decision). `ClaudeUnavailable.reason` ∈ not_installed |
  logged_out | rate_limited | offline | timeout | error. Chat paths emit `claude_unavailable` + `done` (the UI
  notice links to Settings). Other routes return `{"error": friendly text}`.

## What NOT to do
- Don't put anything from the system prompt in `claude` argv (see Claude mode above). Don't let Claude
  mode silently fall back to the local model, and don't give the CLI its own tools or MCP servers. The
  confirm gate lives in the daemon's executor.
- Don't locate `claude` with PATH alone. A daemon launched from the desktop or systemd has no
  `~/.local/bin` on its PATH, and it reported a working install as "not installed". Use
  `claude_cli._resolve_cli`, which falls back to the standard install locations.
- Don't run a second model server "just in case" — one Ollama instance, one idle-unload policy.
  (This rule pre-dated the llama.cpp experiment above and was correct; the experiment cost a day
  to arrive back at it. Check this list *before* benchmarking an alternative runtime.)
- Don't disable idle-unload to shave latency — the point of this app is it's always available *and* doesn't cook the laptop.
- Don't "fix" cold-start by trimming the IDENTITY prompt without weighing it against the anti-fabrication behavior it encodes — that's a product/behavior call, not a free optimization (Phase 11 finding).
