"""Shared Google OAuth (installed-app flow). One client + one token file serve
the calendar poller, the gcal MCP server, and (Phase 6) Gmail — never in SQLite,
never committed. Not-connected is a normal state, not an error."""

import json
from pathlib import Path

READ_SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)
WRITE_SCOPES = READ_SCOPES + ("https://www.googleapis.com/auth/calendar.events",)
# What lumen-google-auth requests today. Write scope joined when the Phase 5
# write half shipped (staged scopes — never request ahead of the feature).
SCOPES = WRITE_SCOPES


def load_credentials(google_cfg, scopes=READ_SCOPES):
    """Credentials from the token file, or None when not connected / not granted.
    No eager refresh — the API client refreshes in-memory on first use."""
    try:
        data = json.loads(Path(google_cfg.token_path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("refresh_token"):
        return None
    if not set(scopes) <= set(data.get("scopes", [])):
        return None  # token predates these scopes — needs a re-consent run
    from google.oauth2.credentials import Credentials
    return Credentials.from_authorized_user_info(data, scopes=list(scopes))


def connected(google_cfg, scopes=READ_SCOPES) -> bool:
    return load_credentials(google_cfg, scopes) is not None


def save_token(google_cfg, creds) -> None:
    path = Path(google_cfg.token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    path.chmod(0o600)


def run_consent_flow(google_cfg, scopes=SCOPES):
    """Opens the browser consent screen once and stores the refresh token."""
    if not Path(google_cfg.client_secret_path).exists():
        raise SystemExit(
            f"lumen: no OAuth client file at {google_cfg.client_secret_path} — "
            "see docs/google-oauth-setup.md for the one-time setup")
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(
        str(google_cfg.client_secret_path), scopes=list(scopes))
    creds = flow.run_local_server(port=0)
    save_token(google_cfg, creds)
    return creds


def main() -> None:
    """`uv run lumen-google-auth` — the one-time (or re-consent) browser flow."""
    from lumen.daemon.config import load_config
    cfg = load_config()
    run_consent_flow(cfg.google, SCOPES)
    print(f"Connected. Token saved to {cfg.google.token_path}")
