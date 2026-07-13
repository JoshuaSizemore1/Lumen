# Phase 7 (part 1) — Email compose & send: Design

Date: 2026-07-13. Builds on the Phase 6 mirror (`connectors/email_menu.py`), the
confirm-over-IPC broker (`daemon/confirm.py`), the NL-extraction → validate →
dialog pattern from calendar creation (`daemon/llm/event_create.py`), and the live
`ui_v2` MailScreen. Writing-style derivation (Phase 7 part 2) is **not** in this
slice — the revise box uses the fast model with a plain instruction; the style
ruleset plugs into the same draft/revise prompts later.

Design gate passed 2026-07-13: the user chose **in-popup revision** (a "Ask Lumen
to revise…" field inside the compose window) and **Compose + Reply** scope, then
approved the presented design ("yes now implement this").

Trigger for the phase: live testing showed the model inventing policy ("I only
access your local mirror") when asked to send — because no send path existed at
all. The fix is a real compose path plus one identity line saying it exists.

## User-visible behavior

**The compose popup.** One dialog serves every path — an in-window overlay card
(like the confirm overlay, but editable) with To, Cc, Bcc, Subject, and Body
fields, a one-line "Ask Lumen to revise…" field with a Revise button, and
Cancel / Send buttons.

- **Send is the confirmation.** The user is looking at the exact recipients and
  body; clicking Send sends it — no second dialog. This satisfies the
  write-confirmation rule: nothing sends except by this explicit click
  (`email-integration.md`'s "never auto-send" holds — the model cannot send,
  only pre-fill the popup).
- **Revise**: type an instruction ("shorter, more casual"), hit Revise; subject
  and body refresh in place from the fast model. Recipient fields are never
  touched by revision. The button shows a busy state while the model works.
- **Failure keeps the draft**: if Gmail can't be reached the popup stays open
  with the error — nothing typed is lost.
- Attachments, rich text/HTML, and drafts-in-Gmail are out of scope.

**Mail screen.** A **Compose** button (header, next to refresh) opens the popup
empty. The existing **Reply** button on an open message loses its mock and opens
the popup pre-filled: To = the sender's address, Subject = "Re: …", and the send
threads under the original conversation in Gmail. Sent mail lands in the mirror
via the normal incremental sync (SENT is inside the mirrored window).

**Chat.** "Email Sarah about rescheduling Friday" / "reply to Sam's email saying
I'm in" routes to a dedicated compose path (same shape as NL event creation):
one structured extraction on the fast model produces {to, cc, subject, body,
reply-hint}, the daemon opens the popup pre-filled over IPC, and the chat turn
waits. The turn then ends in one of: "Sent." (user clicked Send — the daemon
sends with whatever final edits the popup carried), "Cancelled — nothing was
sent." (Cancel/esc/window closed), or an honest failure ("couldn't reach
Gmail…"). For a reply-shaped request the daemon finds the referenced message in
the local mirror (newest match on sender/subject words) and pre-fills reply
threading; if nothing matches it opens a plain compose.

- Addresses the model may pre-fill in To/Cc: only ones the user explicitly
  wrote, **or** (reply path) the replied-to sender from the mirror. A name is
  not an address — the To field is left for the user rather than guessed. The
  popup being editable is why this can be softer than the calendar gate:
  invalid entries are dropped, never a hard refusal.
- The identity block gains one line: Lumen can draft emails which open in a
  compose window for the user to review and send. This kills the invented
  "local mirror only" refusals for messages the compose route doesn't catch.

## Architecture

- **`daemon/llm/email_compose.py`** (new; `event_create.py` template):
  `propose_email()` — one structured generation, JSON out, no tools;
  `validate_draft()` — mechanical gate (address format, user-typed-or-reply-
  sender rule, trimmed strings; always yields an editable draft unless the
  reply is unparseable); `revise_email()` — draft + instruction → revised
  {subject, body}, JSON out.
- **`connectors/email_menu.py`** — `GmailSync.send(to, cc, bcc, subject, body,
  reply_to=None) -> bool`: builds an RFC822 message
  (`email.message.EmailMessage`, base64url raw), `users().messages().send`.
  For a reply: the mirror row supplies `threadId`; one metadata `get` fetches
  the original's Message-ID for In-Reply-To/References so recipients' clients
  thread too. Uses the existing write service (`gmail.modify` **already
  authorizes send** — no new scope, no re-consent; the dev-plan's "add
  gmail.send" is satisfied by scopes already granted).
- **`daemon/confirm.py`** — `resolve()` stops coercing to bool and `wait()`
  takes an optional per-call timeout, so the same broker carries the compose
  round-trip (the popup's answer is fields, not a yes/no; editing needs longer
  than a confirm click — 30 min). Existing bool callers are unchanged.
- **Router** —
  - `COMPOSE_HINT` route (checked ahead of the other chat paths):
    `_compose_email_chat` = propose → resolve reply target from the mirror →
    yield `compose_request` (+`compose_id`) → await the broker → send or
    cancel, with the outcome as the persisted chat turn.
  - `emails.send` one-shot (popup opened from the Mail screen): validate,
    `GmailSync.send`, `{ok, message}` back. No confirm gate — the popup **is**
    the gate.
  - `emails.revise` one-shot: `revise_email` on the fast model.
  - `compose.response` (dedicated confirm-client channel, like
    `confirm.response`): resolves the broker with the popup's final fields or
    a cancel. If the id already expired (edit took >30 min) but the action is
    Send, the router sends anyway and answers like `emails.send` — a click on
    Send must never be silently dropped.
- **UI (`ui_v2`)** — new `compose.py` `ComposeDialog` overlay; `DaemonClient`
  learns the `compose_request` event and a `respond_compose()` sender;
  `AppState` gains `compose_requested` signal, `open_compose()` (local, no
  daemon), `send_email()`, `revise_email()`, `respond_compose()`; the mock
  `email_confirm`/`reply_confirm` payloads are deleted. `_norm_mail` starts
  carrying the sender's bare address (Reply needs it; the list shows the name
  as before). `LumenWindow` hosts the dialog beside the confirm overlay and
  surfaces the window when a chat-driven compose arrives (same rule as
  confirms). Sample mode still works: Send toasts, Revise no-ops.

## Testing

- `email_compose`: JSON extraction; address gate (typed vs invented vs reply
  sender); revise round-trip; unparseable-reply failure text.
- `GmailSync.send` against a fake service: MIME shape, cc/bcc, threading
  (threadId + In-Reply-To/References from the metadata get), API failure →
  False.
- Broker: payload passthrough, per-call timeout, deny_all still cancels.
- Router: COMPOSE_HINT routing (and read-shaped mail questions NOT routed);
  full chat round-trip send/cancel/edited-fields; expired-id Send fallback;
  `emails.send` validation + result; `emails.revise`.
- UI: dialog prefill/edit/send/cancel wiring in both modes (compose_id vs
  Mail-screen), revise busy state + field refresh, Reply prefill with address
  + threading id, client event parsing (offscreen Qt, existing patterns).
- Live verification: compose from the Mail screen to the user's own address;
  chat-driven draft → edit → send; reply threads correctly in Gmail; revise
  reshapes the draft; cancel sends nothing.

## Out of scope (this slice)

Writing-style ruleset derivation/application (Phase 7 part 2 — plugs into these
same prompts); attachments; HTML mail; saving drafts to Gmail; label editing;
scheduling sends; multiple accounts.
