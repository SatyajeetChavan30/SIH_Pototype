"""Sign-in and role-based access (stdlib only: PBKDF2 password hashes, HMAC-signed session cookie).

Roles
  field   rig-site / driller: live alerts, map, correlation, risk, knowledge, expert memos, alert acknowledgement
  office  drilling engineer / RTOC: everything a field user has, plus document ingestion, the review queue,
          after-action review approval, the what-if planner and analytics
  admin   office rights plus user management and decision-log verification

The access policy lives in one table (`RULES`) and is enforced by a middleware in `api/main.py`, so an endpoint
cannot forget its check. The signed-in user is also the `actor` written to the decision log: an acknowledgement or
approval names a person, not whatever the browser claimed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from . import config
from .db import DB

ROLES = ("field", "office", "admin")
OFFICE = ("office", "admin")
COOKIE = "nwis_session"
SESSION_S = 24 * 3600          # a rig tablet must survive a 12-hour tour plus handover without re-login
_ITER = 200_000

# (method or None for any, path prefix, roles allowed). First match wins. An empty role tuple means public;
# /api/* paths that match no rule need a sign-in with any role.
RULES: list[tuple[str | None, str, tuple[str, ...]]] = [
    (None, "/api/health", ()),
    (None, "/api/auth/", ()),
    ("POST", "/api/ingest", OFFICE),
    (None, "/api/review", OFFICE),
    ("POST", "/api/aar/", OFFICE),
    ("POST", "/api/risk/whatif", OFFICE),
    (None, "/api/analytics", OFFICE),
    (None, "/api/jobs", OFFICE),        # admin-only kinds (knowledge-base rebuilds) are checked by the endpoint
    (None, "/api/audit/verify", ("admin",)),
    (None, "/api/users", ("admin",)),
]

DEMO_USERS = [
    ("field", "A. Gogoi (driller)", "field"),
    ("office", "R. Baruah (drilling engineer)", "office"),
    ("admin", "S. Dutta (RTOC admin)", "admin"),
]


def enabled() -> bool:
    return config.AUTH


def ensure_schema(db: DB) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, display_name TEXT, role TEXT, "
               "salt TEXT, pw_hash TEXT, created TEXT)")
    db.commit()


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER).hex()


def create_user(db: DB, username: str, display_name: str, role: str, password: str) -> dict:
    username = username.strip().lower()
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if not username or len(password) < 4:
        raise ValueError("username and a password of at least 4 characters are required")
    if db.one("SELECT username FROM users WHERE username=?", (username,)):
        raise ValueError(f"user {username} already exists")
    salt = secrets.token_hex(16)
    db.insert("users", {"username": username, "display_name": display_name.strip() or username, "role": role,
                        "salt": salt, "pw_hash": _hash(password, salt),
                        "created": time.strftime("%Y-%m-%dT%H:%M:%S")})
    db.commit()
    return public_user(db.one("SELECT * FROM users WHERE username=?", (username,)))


def ensure_demo_users(db: DB) -> None:
    """Seed one account per role on an empty user table (password: config.DEMO_PASSWORD)."""
    ensure_schema(db)
    if db.one("SELECT COUNT(*) n FROM users")["n"]:
        return
    for username, name, role in DEMO_USERS:
        create_user(db, username, name, role, config.DEMO_PASSWORD)


def public_user(row: dict | None) -> dict | None:
    return None if row is None else {"username": row["username"], "display_name": row["display_name"], "role": row["role"]}


def list_users(db: DB) -> list[dict]:
    return [public_user(r) for r in db.query("SELECT * FROM users ORDER BY role, username")]


def authenticate(db: DB, username: str, password: str) -> dict | None:
    row = db.one("SELECT * FROM users WHERE username=?", ((username or "").strip().lower(),))
    if row is None:
        _hash(password or "", "00" * 16)   # same work whether or not the user exists
        return None
    return public_user(row) if hmac.compare_digest(row["pw_hash"], _hash(password or "", row["salt"])) else None


def _secret(db: DB) -> bytes:
    if config.SECRET:
        return config.SECRET.encode()
    s = db.kv_get("auth_secret")
    if not s:
        s = secrets.token_hex(32)
        db.kv_set("auth_secret", s)
    return bytes.fromhex(s)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def issue_token(db: DB, user: dict) -> str:
    body = _b64(json.dumps({"u": user["username"], "exp": int(time.time()) + SESSION_S}).encode())
    return body + "." + _b64(hmac.new(_secret(db), body.encode(), hashlib.sha256).digest())


def user_from_token(db: DB, token: str | None) -> dict | None:
    """The user a session cookie belongs to, or None (missing, forged, expired, or user deleted)."""
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _b64(hmac.new(_secret(db), body.encode(), hashlib.sha256).digest())):
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return None
    if claims.get("exp", 0) < time.time():
        return None
    return public_user(db.one("SELECT * FROM users WHERE username=?", (claims.get("u"),)))


def required_roles(method: str, path: str) -> tuple[str, ...] | None:
    """Roles allowed for a request: () = public, None = any signed-in user, otherwise the listed roles."""
    for m, prefix, roles in RULES:
        if (m is None or m == method) and path.startswith(prefix):
            return roles
    return None


def can(user: dict | None, method: str, path: str) -> bool:
    roles = required_roles(method, path)
    if roles == ():
        return True
    return user is not None and (roles is None or user["role"] in roles)
