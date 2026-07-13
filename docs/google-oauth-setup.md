# Connecting Lumen to Google Calendar & Gmail (one-time, ~10 minutes)

Google requires every app that reads your calendar or mail to have its own
"OAuth client" — there is no shared key Lumen could ship. You create one under
your own Google account, download a small file, and run one command. After
that, Lumen stays connected on its own.

Nothing here costs money, and nothing leaves your machine except Lumen talking
directly to Google's Calendar and Gmail APIs.

## Step 1 — Create a Google Cloud project

1. Open https://console.cloud.google.com/ and sign in with your Google account
   (joshjsizemore@gmail.com).
2. Click the project picker (top-left, next to "Google Cloud") → **New project**.
3. Name it `lumen` (anything works) → **Create**, then make sure it's selected
   in the picker.

## Step 2 — Enable the Calendar and Gmail APIs

1. In the top search bar, search **Google Calendar API** and open it.
2. Click **Enable**.
3. Do the same for the **Gmail API**: search it, open it, click **Enable**.

> Enabling an API is separate from granting permissions on the consent screen.
> If only Calendar is enabled, the Gmail consent still "succeeds" — but every
> mail sync then fails with a 403 `accessNotConfigured` and the inbox stays
> empty (this bit us on 2026-07-13).

## Step 3 — Set up the consent screen

1. Search **OAuth consent screen** and open it (under "Google Auth Platform").
2. If asked, choose **External** and click **Create**.
3. Fill only the required fields: app name `lumen`, your email as the support
   email and developer contact. **Save and continue** through the remaining
   screens — no scopes or extra info needed here.
4. Under **Audience** (or "Test users"), click **Add users** and add your own
   email address. This keeps the app in "testing" mode, which is exactly right
   for a personal tool.

> Note: apps in testing mode get refresh tokens that Google expires after 7 days
> **only if** the "publishing status" is *Testing* AND the user isn't a listed
> test user — adding yourself as a test user avoids the constant re-login.

## Step 4 — Create the Desktop OAuth client

1. Search **Credentials** (APIs & Services → Credentials).
2. **Create credentials → OAuth client ID**.
3. Application type: **Desktop app**. Name: `lumen-desktop`. **Create**.
4. Click **Download JSON** on the new client.
5. Save/move that file to exactly:

   ```
   ~/.local/share/lumen/google/client_secret.json
   ```

   (create the folders if needed: `mkdir -p ~/.local/share/lumen/google`)

## Step 5 — Connect Lumen

From the Lumen project folder:

```
uv run lumen-google-auth
```

Your browser opens a Google consent screen — you may need to click
"Advanced → Go to lumen (unsafe)" (it's your own app; Google shows this for all
unverified apps). Approve, and the terminal prints
`Connected. Token saved to …/token.json`.

That's it. Within one poll interval (5 minutes — or restart the daemon for
immediately) the dashboard's calendar column shows your real events.

## Checking it worked

- Dashboard shows today's events and a "● synced …" stamp instead of
  "not connected".
- Ask the launcher: *"what's on my calendar this week?"*

## Housekeeping

- The token lives at `~/.local/share/lumen/google/token.json` (file mode 600,
  never in the database, never committed to git).
- **Disconnect / revoke**: delete `token.json`, and optionally remove lumen's
  access at https://myaccount.google.com/permissions.
- **Re-consent**: when Lumen adds a capability that needs a new permission
  (e.g. creating events, or Gmail in Phase 6), just run `uv run
  lumen-google-auth` again — same flow, one click.
