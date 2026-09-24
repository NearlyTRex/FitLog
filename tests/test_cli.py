import runpy
import sys

import pyotp
import pytest

from fitlog import auth, cli
from fitlog.db import Database
from conftest import write

PASSWORD = "correct horse battery"


@pytest.fixture
def env(tmp_path, monkeypatch, catalog_dir):
    monkeypatch.setenv("FITLOG_DB", str(tmp_path / "cli.db"))
    monkeypatch.setenv("FITLOG_CATALOG_DIR", str(catalog_dir))
    return tmp_path / "cli.db"


def passwords(monkeypatch, *answers):
    answers = iter(answers)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))


def test_create_user_shows_the_authenticator(env, monkeypatch, capsys):
    passwords(monkeypatch, PASSWORD, PASSWORD)
    assert cli.main(["user", "create", "me"]) == 0
    out = capsys.readouterr().out
    secret = out.split("Or enter this key manually: ")[1].split()[0]
    assert "Created user 'me'." in out and "otpauth://totp/FitLog:me" in out
    assert "█" in out or "#" in out
    db = Database(env)
    assert auth.authenticate(db, "me", PASSWORD, pyotp.TOTP(secret).now(), "1.1.1.1") is not None


def test_mismatched_passwords(env, monkeypatch, capsys):
    passwords(monkeypatch, PASSWORD, PASSWORD + "x")
    assert cli.main(["user", "create", "me"]) == 1
    assert "Passwords do not match" in capsys.readouterr().err


def test_reset_password_and_totp(env, monkeypatch, capsys):
    auth.create_user(Database(env), "me", PASSWORD)
    passwords(monkeypatch, "a whole new password", "a whole new password")
    assert cli.main(["user", "reset-password", "me"]) == 0
    assert cli.main(["user", "reset-totp", "me"]) == 0
    out = capsys.readouterr().out
    assert "Password changed" in out and "Authenticator reset" in out
    assert cli.main(["user", "reset-totp", "nobody"]) == 1


def test_check(env, catalog_dir, tmp_path, capsys):
    assert cli.main(["check"]) == 0
    assert "2 foods, 8 exercises, 0 errors" in capsys.readouterr().out
    bad = tmp_path / "bad"
    write(bad / "foods" / "x.yaml", "name: X\n")
    assert cli.main(["check", str(bad)]) == 1
    captured = capsys.readouterr()
    assert "error: foods/x.yaml" in captured.err and "1 errors" in captured.out


def test_serve(env, monkeypatch):
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    assert cli.main(["serve", "--port", "9999"]) == 0
    assert calls == [{"host": "127.0.0.1", "port": 9999, "proxy_headers": False}]


def test_module_entry_point(env, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["fitlog", "check"])
    with pytest.raises(SystemExit) as exit:
        runpy.run_module("fitlog", run_name="__main__")
    assert exit.value.code == 0
    assert "0 errors" in capsys.readouterr().out
