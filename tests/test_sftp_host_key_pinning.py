"""SFTP host keys are pinned persistently and never silently replaced."""

import paramiko
import pytest

from home_media_catalog.playback_browser import _sftp_connection


class _SimulatedSSH:
    presented_key = None

    def __init__(self):
        self.policy = None
        self.keys = paramiko.HostKeys()

    def load_system_host_keys(self):
        pass

    def load_host_keys(self, filename):
        # The production policy also reloads this persisted file under a lock.
        pass

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, **kwargs):
        self.policy.missing_host_key(self, kwargs["hostname"], self.presented_key)

    def get_host_keys(self):
        return self.keys

    def open_sftp(self):
        return self

    def close(self):
        pass


def test_first_use_pins_host_key_and_rejects_changed_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(paramiko, "SSHClient", _SimulatedSSH)
    host = "192.168.1.10"
    location = {"host": host, "port": 22, "username": "user", "target": "sftp://192.168.1.10/"}

    original = paramiko.RSAKey.generate(1024)
    _SimulatedSSH.presented_key = original
    _sftp_connection(location, "secret")
    pinned = tmp_path / "ssh" / "known_hosts"
    assert pinned.exists()
    assert paramiko.HostKeys().get(host) is None
    known = paramiko.HostKeys()
    known.load(str(pinned))
    assert known.lookup(host)[original.get_name()] == original

    # Repeated connections to the same key succeed.
    _sftp_connection(location, "secret")

    # A substituted host key must fail, without overwriting the saved pin.
    changed = paramiko.RSAKey.generate(1024)
    _SimulatedSSH.presented_key = changed
    with pytest.raises(paramiko.BadHostKeyException):
        _sftp_connection(location, "secret")
    restored = paramiko.HostKeys()
    restored.load(str(pinned))
    assert restored.lookup(host)[original.get_name()] == original


@pytest.mark.parametrize(
    ("username", "password"),
    [("", "secret"), ("user", "")],
)
def test_sftp_missing_server_credentials_returns_actionable_conflict(
    tmp_path, monkeypatch, username, password
):
    from fastapi import HTTPException

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    def unexpected_connection():
        raise AssertionError("SSH must not connect without server credentials")

    monkeypatch.setattr(paramiko, "SSHClient", unexpected_connection)
    location = {"host": "192.168.1.10", "username": username, "kind": "sftp"}
    with pytest.raises(HTTPException) as error:
        _sftp_connection(location, password)

    assert error.value.status_code == 409
    assert "server-side username and password" in error.value.detail
    assert "iOS" in error.value.detail
    assert "secret" not in error.value.detail


def test_rejected_sftp_password_returns_actionable_error(tmp_path, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    class RejectingSSH(_SimulatedSSH):
        closed = False

        def connect(self, **kwargs):
            raise paramiko.AuthenticationException("Authentication failed")

        def close(self):
            self.closed = True

    session = RejectingSSH()
    monkeypatch.setattr(paramiko, "SSHClient", lambda: session)
    with pytest.raises(HTTPException) as error:
        _sftp_connection({"host": "192.168.1.10", "username": "user"}, "secret")

    assert error.value.status_code == 409
    assert "server-side username" in error.value.detail
    assert "secret" not in error.value.detail
    assert session.closed
