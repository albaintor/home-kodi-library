"""Older NAS FTP servers may implement LIST without MLSD."""

import ftplib

from home_media_catalog import playback_browser


class FakeLegacyFTP:
    def __init__(self):
        self.commands = []

    def mlsd(self, folder):
        raise ftplib.error_perm("502 MLSD not implemented")

    def retrlines(self, command, callback):
        self.commands.append(command)
        callback("drwxr-xr-x 2 user group 4096 Jan 1 12:00 Films")
        callback("-rw-r--r-- 1 user group 123456 Jan 1 12:00 Movie file.mkv")
        callback("lrwxrwxrwx 1 user group 5 Jan 1 12:00 escape -> /root")

    def quit(self):
        pass


def test_ftp_legacy_list_fallback(monkeypatch):
    ftp = FakeLegacyFTP()
    monkeypatch.setattr(playback_browser, "_ftp_connection", lambda *_: ftp)
    location = {"kind": "ftp", "host": "192.168.1.10", "share": "Media"}
    rows = playback_browser._sync_browse(location, "", "password", {})
    assert ftp.commands == ["LIST Media"]
    assert rows == [
        {"name": "Films", "is_dir": True, "size": 4096},
        {"name": "Movie file.mkv", "is_dir": False, "size": 123456},
    ]
