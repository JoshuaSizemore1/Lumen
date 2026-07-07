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

## Model choice
- Default/fast-path model: something in the 3B–8B range, Q4 quantization (e.g. Qwen2.5 7B-Instruct-Q4, or a 3B model if you want faster cold-starts on integrated graphics). This handles routing, simple Q&A, and summarization.
- Escalation model (optional): only invoke a larger model for tasks the router flags as needing it (long-context reasoning, complex synthesis across many emails). Keep this the exception, not the default — every escalation is a bigger RAM/thermal hit.
- Benchmark cold-start latency on the actual Zenbook Duo hardware before committing to a model size. If it's not sub-2-second on a cold load, the "quick-launcher" UX will feel broken.

## Router behavior
`daemon/llm/` should expose a single entrypoint that:
1. Picks fast-path vs escalation model based on request classification (keep this classification cheap — a regex/heuristic pass is fine before reaching for the LLM itself to decide).
2. Sends `keep_alive` explicitly on every request rather than relying only on the service-level default, so behavior is consistent even if the systemd override gets lost.
3. Never blocks the UI on a cold model load without showing a loading state — first request after idle-unload will be slower.

## What NOT to do
- Don't run a second model server "just in case" — one Ollama instance, one idle-unload policy.
- Don't disable idle-unload to shave latency — the point of this app is it's always available *and* doesn't cook the laptop.
