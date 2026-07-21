import keyring
import keyring.backend
import keyring.errors
import pytest

from lumen.ui_v3 import canvas_creds


class MemKeyring(keyring.backend.KeyringBackend):
    """In-memory keyring so tests never touch the real Secret Service."""
    priority = 1

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service, user, password):
        self._store[(service, user)] = password

    def get_password(self, service, user):
        return self._store.get((service, user))

    def delete_password(self, service, user):
        if (service, user) not in self._store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self._store[(service, user)]


@pytest.fixture
def mem_keyring():
    prev = keyring.get_keyring()
    keyring.set_keyring(MemKeyring())
    yield
    keyring.set_keyring(prev)


def test_save_then_load_roundtrips(mem_keyring):
    assert canvas_creds.load() is None
    canvas_creds.save("u1234567", "hunter2")
    assert canvas_creds.load() == ("u1234567", "hunter2")


def test_forget_clears_both(mem_keyring):
    canvas_creds.save("u1", "p1")
    canvas_creds.forget()
    assert canvas_creds.load() is None


def test_forget_when_empty_is_safe(mem_keyring):
    canvas_creds.forget()          # must not raise
    assert canvas_creds.load() is None


def test_load_none_when_password_missing(mem_keyring):
    keyring.set_password("lumen-canvas", "unid", "u1")   # uNID only, no password
    assert canvas_creds.load() is None
