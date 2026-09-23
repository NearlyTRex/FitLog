import pyotp
import pytest

from fitlog import auth

NOW = 1_800_000_000
PASSWORD = "correct horse battery"


@pytest.fixture
def secret(db):
    return auth.create_user(db, "me", PASSWORD)


def code(secret, now=NOW):
    return pyotp.TOTP(secret).at(now)


def test_only_one_user(db, secret):
    with pytest.raises(auth.AuthError):
        auth.create_user(db, "other", PASSWORD)


def test_short_password_rejected(db):
    with pytest.raises(auth.AuthError):
        auth.create_user(db, "me", "short")


def test_password_is_hashed(db, secret):
    with db.connect() as conn:
        stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert stored.startswith("$argon2id$") and PASSWORD not in stored


def test_login_success(db, secret):
    assert auth.authenticate(db, "me", PASSWORD, code(secret), "1.1.1.1", NOW) is not None


def test_login_accepts_one_step_of_drift(db, secret):
    assert auth.authenticate(db, "me", PASSWORD, code(secret, NOW - 30), "1.1.1.1", NOW) is not None


@pytest.mark.parametrize("username,password,use_code", [
    ("me", "wrong password!", True),
    ("me", PASSWORD, False),
    ("nobody", PASSWORD, True),
])
def test_login_failures(db, secret, username, password, use_code):
    sent = code(secret) if use_code else "000000"
    assert auth.authenticate(db, username, password, sent, "1.1.1.1", NOW) is None


def test_code_cannot_be_replayed(db, secret):
    assert auth.authenticate(db, "me", PASSWORD, code(secret), "1.1.1.1", NOW) is not None
    assert auth.authenticate(db, "me", PASSWORD, code(secret), "1.1.1.1", NOW + 5) is None
    assert auth.authenticate(db, "me", PASSWORD, code(secret, NOW + 30), "1.1.1.1", NOW + 30) is not None


def test_lockout_per_ip(db, secret):
    for _ in range(auth.MAX_FAILURES_PER_IP):
        auth.authenticate(db, "me", "wrong password!", "000000", "1.1.1.1", NOW)
    assert auth.is_locked(db, "1.1.1.1", NOW)
    assert auth.authenticate(db, "me", PASSWORD, code(secret), "1.1.1.1", NOW) is None
    assert not auth.is_locked(db, "2.2.2.2", NOW)
    assert not auth.is_locked(db, "1.1.1.1", NOW + auth.ATTEMPT_WINDOW + 1)
    assert auth.authenticate(db, "me", PASSWORD, code(secret, NOW + 999), "1.1.1.1", NOW + 999) is not None


def test_global_lockout(db, secret):
    for i in range(auth.MAX_FAILURES_GLOBAL):
        auth.authenticate(db, "me", "wrong password!", "000000", f"10.0.0.{i}", NOW)
    assert auth.is_locked(db, "9.9.9.9", NOW)


def test_sessions(db, secret):
    user_id = auth.authenticate(db, "me", PASSWORD, code(secret), "1.1.1.1", NOW)
    token, csrf = auth.create_session(db, user_id, 1, NOW)
    session = auth.get_session(db, token, NOW)
    assert session["username"] == "me" and session["csrf_token"] == csrf
    assert auth.get_session(db, token, NOW + 86401) is None
    assert auth.get_session(db, "forged", NOW) is None
    auth.delete_session(db, token)
    assert auth.get_session(db, token, NOW) is None


def test_credential_resets_end_sessions(db, secret):
    token, _ = auth.create_session(db, 1, 1, NOW)
    auth.set_password(db, "me", "another long password")
    assert auth.get_session(db, token, NOW) is None
    assert auth.authenticate(db, "me", PASSWORD, code(secret), "1.1.1.1", NOW) is None

    token, _ = auth.create_session(db, 1, 1, NOW)
    new_secret = auth.reset_totp(db, "me")
    assert auth.get_session(db, token, NOW) is None
    assert auth.authenticate(db, "me", "another long password", code(new_secret), "1.1.1.1", NOW) is not None
