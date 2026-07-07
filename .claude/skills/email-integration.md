# Skill: Email Integration (Gmail)

## Auth
OAuth2 via Google's installed-app flow. Store refresh token in `.env` or a local credentials file — never in SQLite, never committed.

## Scopes
Start minimal:
- `gmail.readonly` for sync/summarization
- Add `gmail.send` and `gmail.modify` only when write features (send, archive, mark-read) are actually implemented — don't request scopes ahead of the feature.

## Sync strategy
- Poll on a timer (default 5 min, configurable in `config.toml`) rather than standing up Gmail push notifications (Pub/Sub) — polling is simpler for a single-user local app and the latency tradeoff doesn't matter here.
- Cache message metadata (sender, subject, snippet, timestamp, label) in SQLite. Don't re-fetch full message bodies on every poll — only fetch body when the LLM/UI actually needs it for a specific message.

## Write actions
Any send/archive/delete/mark-read action:
1. Router builds the action but does not execute it.
2. UI shows a confirmation (what will happen, to whom, exact content if sending).
3. Only on explicit user confirmation does the connector call the Gmail API to execute.

## What the LLM should be able to do
- Summarize unread/recent mail
- Answer "did X email me back" / "what did Y say about Z" type queries against cached metadata + fetched bodies
- Draft replies (draft only, never auto-send)

## What NOT to do
- Don't auto-send anything, ever, regardless of how confident the router is.
- Don't fetch entire mailbox history on first run — bound initial sync (e.g. last 30 days) and backfill lazily if needed.
