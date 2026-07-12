"""Shared Google OAuth: token loading, scope checks, secure token storage.
No network — tokens are fabricated files; the consent flow itself is live-only."""

import json

from lumen.daemon.config import GoogleConfig
from lumen.daemon.connectors import google_auth
from lumen.daemon.connectors.google_auth import (
    READ_SCOPES, WRITE_SCOPES, connected, load_credentials, save_token,
)


def cfg(tmp_path) -> GoogleConfig:
    return GoogleConfig(client_secret_path=tmp_path / "cs.json",
                        token_path=tmp_path / "token.json")


def write_token(tmp_path, scopes=list(READ_SCOPES), refresh="rt"):
    data = {"client_id": "cid", "client_secret": "cs", "refresh_token": refresh,
            "token": "at", "scopes": scopes,
            "token_uri": "https://oauth2.googleapis.com/token"}
    (tmp_path / "token.json").write_text(json.dumps(data))


def test_missing_token_file_means_not_connected(tmp_path):
    assert load_credentials(cfg(tmp_path)) is None
    assert connected(cfg(tmp_path)) is False


def test_valid_token_loads_credentials(tmp_path):
    write_token(tmp_path)
    creds = load_credentials(cfg(tmp_path), READ_SCOPES)
    assert creds is not None and creds.refresh_token == "rt"
    assert connected(cfg(tmp_path)) is True


def test_token_lacking_requested_scopes_means_not_connected(tmp_path):
    write_token(tmp_path, scopes=list(READ_SCOPES))
    assert load_credentials(cfg(tmp_path), WRITE_SCOPES) is None


def test_write_scopes_include_read():
    assert set(READ_SCOPES) <= set(WRITE_SCOPES)


def test_malformed_token_file_means_not_connected(tmp_path):
    (tmp_path / "token.json").write_text("{not json")
    assert load_credentials(cfg(tmp_path)) is None


def test_token_without_refresh_token_means_not_connected(tmp_path):
    write_token(tmp_path, refresh=None)
    assert load_credentials(cfg(tmp_path)) is None


def test_save_token_creates_dir_and_chmods_600(tmp_path):
    class FakeCreds:
        def to_json(self):
            return json.dumps({"refresh_token": "rt"})

    c = GoogleConfig(client_secret_path=tmp_path / "cs.json",
                     token_path=tmp_path / "deep" / "dir" / "token.json")
    save_token(c, FakeCreds())
    assert c.token_path.read_text() == json.dumps({"refresh_token": "rt"})
    assert (c.token_path.stat().st_mode & 0o777) == 0o600


def test_consent_flow_without_client_secret_exits_with_pointer(tmp_path):
    import pytest
    with pytest.raises(SystemExit, match="google-oauth-setup"):
        google_auth.run_consent_flow(cfg(tmp_path), READ_SCOPES)


def test_gmail_scopes_staged_into_consent():
    assert "https://www.googleapis.com/auth/gmail.readonly" in google_auth.GMAIL_READ_SCOPES
    assert "https://www.googleapis.com/auth/gmail.modify" in google_auth.GMAIL_WRITE_SCOPES
    # the one-time consent run now covers calendar + gmail together
    assert set(google_auth.WRITE_SCOPES) <= set(google_auth.SCOPES)
    assert set(google_auth.GMAIL_WRITE_SCOPES) <= set(google_auth.SCOPES)
