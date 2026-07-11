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

## Router behavior
`daemon/llm/` should expose a single entrypoint that:
1. Picks fast-path vs escalation model based on request classification (keep this classification cheap — a regex/heuristic pass is fine before reaching for the LLM itself to decide).
2. Sends `keep_alive` explicitly on every request rather than relying only on the service-level default, so behavior is consistent even if the systemd override gets lost.
3. Never blocks the UI on a cold model load without showing a loading state — first request after idle-unload will be slower.

## What NOT to do
- Don't run a second model server "just in case" — one Ollama instance, one idle-unload policy.
- Don't disable idle-unload to shave latency — the point of this app is it's always available *and* doesn't cook the laptop.
