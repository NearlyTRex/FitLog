"""Single-user authentication: argon2 password, TOTP second factor, server-side sessions."""

import hashlib
import hmac
import secrets
import time

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

ATTEMPT_WINDOW = 15 * 60
MAX_FAILURES_PER_IP = 5
MAX_FAILURES_GLOBAL = 20
MIN_PASSWORD_LENGTH = 12
TOTP_ISSUER = "FitLog"

_hasher = PasswordHasher()
_dummy_hash = _hasher.hash(secrets.token_urlsafe(16))


class AuthError(ValueError):
    pass


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _check_password(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")


def create_user(db, username, password):
    """Create the one user. Returns the TOTP secret to enroll in an authenticator app."""
    username = username.strip()
    if not username:
        raise AuthError("Username is required")
    _check_password(password)
    secret = pyotp.random_base32()
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM users").fetchone():
            raise AuthError("A user already exists; use reset-password or reset-totp instead")
        conn.execute(
            "INSERT INTO users (username, password_hash, totp_secret) VALUES (?, ?, ?)",
            (username, _hasher.hash(password), secret),
        )
    return secret


def _user(conn, username):
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        raise AuthError(f"No user named '{username}'")
    return row


def set_password(db, username, password):
    _check_password(password)
    with db.connect() as conn:
        user = _user(conn, username)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_hasher.hash(password), user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))


def reset_totp(db, username):
    secret = pyotp.random_base32()
    with db.connect() as conn:
        user = _user(conn, username)
        conn.execute(
            "UPDATE users SET totp_secret = ?, totp_last_step = 0 WHERE id = ?", (secret, user["id"])
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    return secret


def provisioning_uri(username, secret):
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=TOTP_ISSUER)


def _match_totp(secret, code, last_step, now):
    """Return the matched time step, allowing one step of clock drift and rejecting replays."""
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    current = int(now) // totp.interval
    for step in (current - 1, current, current + 1):
        if step > last_step and hmac.compare_digest(totp.at(step * totp.interval), code):
            return step
    return None


def is_locked(db, ip, now=None):
    now = time.time() if now is None else now
    since = int(now) - ATTEMPT_WINDOW
    with db.connect() as conn:
        conn.execute("DELETE FROM login_attempts WHERE at < ?", (since,))
        per_ip = conn.execute("SELECT COUNT(*) FROM login_attempts WHERE ip = ?", (ip,)).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0]
    return per_ip >= MAX_FAILURES_PER_IP or total >= MAX_FAILURES_GLOBAL


def authenticate(db, username, password, code, ip, now=None):
    """Return the user id on success, else None. Every failure counts toward the lockout."""
    now = time.time() if now is None else now
    if is_locked(db, ip, now):
        return None
    with db.connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
        try:
            _hasher.verify(user["password_hash"] if user else _dummy_hash, password)
            password_ok = user is not None
        except (VerificationError, InvalidHashError):
            password_ok = False
        step = _match_totp(user["totp_secret"], code, user["totp_last_step"], now) if password_ok else None
        if step is None:
            conn.execute("INSERT INTO login_attempts (ip, at) VALUES (?, ?)", (ip, int(now)))
            return None
        conn.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (step, user["id"]))
        conn.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
        if _hasher.check_needs_rehash(user["password_hash"]):
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?", (_hasher.hash(password), user["id"])
            )
        return user["id"]


def create_session(db, user_id, days, now=None):
    """Return (token, csrf_token). Only the token's hash is stored."""
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (int(now),))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), user_id, csrf, int(now + days * 86400)),
        )
    return token, csrf


def get_session(db, token, now=None):
    if not token:
        return None
    now = time.time() if now is None else now
    with db.connect() as conn:
        row = conn.execute(
            "SELECT s.user_id, s.csrf_token, u.username FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at >= ?",
            (_token_hash(token), int(now)),
        ).fetchone()
    return dict(row) if row else None


def delete_session(db, token):
    if token:
        with db.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
