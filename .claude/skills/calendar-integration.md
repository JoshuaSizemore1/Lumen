# Skill: Calendar Integration (Google Calendar)

## Auth
Reuse the same OAuth2 credentials/flow as email where possible (Google supports combined scopes in one consent).

## Scopes
- `calendar.readonly` for viewing/summarizing
- `calendar.events` (write) only once event creation/editing is actually implemented

## Sync strategy
- Poll for events in a rolling window (e.g. today + next 14 days) on the same interval as email sync.
- Cache events in SQLite: title, start/end, location, attendees, description. Refresh the window on each poll rather than doing incremental diffing — the dataset is small enough that this is simpler and cheap.

## Write actions
Same pattern as email: router proposes (e.g. "create event 'Dentist' Tuesday 2pm"), UI shows exact details, user confirms, connector executes. No silent calendar writes.

## What the LLM should be able to do
- "What's on my calendar today/this week"
- "Am I free at 3pm Thursday"
- Natural-language event creation ("book a call with X Friday afternoon") → proposed event → confirm → create

## What NOT to do
- Don't auto-accept/decline invites.
- Don't create recurring events without explicit confirmation of the recurrence rule — recurrence mistakes are annoying to unwind.
