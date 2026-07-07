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
| Fast path / router / tool calls | `qwen3:4b` (Q4, ~2.6GB) | Phase 1 default |
| Tool-chain escalation | Qwen3 14B-class (Q4, ~9GB) | Phase 3+, when MCP chains need it (see `mcp-integration.md`) |
| Writing/synthesis escalation (candidate) | `gemma3:12b-it-qat` (~8GB) | Phase 7–8 benchmark — stronger prose + Japanese |

- The fast-path slot must be a model with native tool-call support in Ollama's `tools` API (Qwen3 qualifies; Gemma 3 does not — never route tool calls to Gemma, prose-only).
- Escalation is the exception, not the default — every escalation is a bigger RAM/thermal hit. Model names live in `config.toml`; swapping a slot is config, not code.
- Thermal ceiling on this hardware (Core Ultra 9 285H, 32GB shared RAM, iGPU only): ~14B-class for regular use. 27B+ fits in RAM but not the thermal envelope.
- Benchmark cold-start latency on the actual hardware before committing to a model size. If it's not sub-2-second on a cold load, the "quick-launcher" UX will feel broken.
- Thinking mode off on the fast path (`think = false`, benchmarked 2026-07-07: ~2900 hidden tokens ≈ 8min CPU per trivial query with it on). Revisit per-slot if an escalation task genuinely benefits.

## Router behavior
`daemon/llm/` should expose a single entrypoint that:
1. Picks fast-path vs escalation model based on request classification (keep this classification cheap — a regex/heuristic pass is fine before reaching for the LLM itself to decide).
2. Sends `keep_alive` explicitly on every request rather than relying only on the service-level default, so behavior is consistent even if the systemd override gets lost.
3. Never blocks the UI on a cold model load without showing a loading state — first request after idle-unload will be slower.

## What NOT to do
- Don't run a second model server "just in case" — one Ollama instance, one idle-unload policy.
- Don't disable idle-unload to shave latency — the point of this app is it's always available *and* doesn't cook the laptop.
