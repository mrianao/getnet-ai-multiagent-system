"""Authentication & authorization at the HTTP boundary.

Opaque bearer tokens (`gnt_` + 256 bits from `secrets`), stored ONLY as SHA-256 hashes in SQLite.
A token resolves to a `Principal` (subject, role, merchant_id). Merchant identity for tools and
conversations comes exclusively from the Principal — never from the request body.

Scope and limits (documented in README): this is local API-caller authentication suitable for a
self-hosted service and for tests. It is not an identity provider: no login UI, no OIDC/SSO, no
MFA, no key rotation service. The `Principal` boundary is where an IdP-issued JWT would plug in.

SHA-256 (not bcrypt/argon2) is appropriate here because tokens are high-entropy random secrets,
not human passwords; brute-forcing a 256-bit preimage is infeasible.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.state.db import Database

TOKEN_PREFIX = "gnt_"
ROLES = ("merchant", "operator")


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    merchant_id: str | None
    token_id: str

    @property
    def is_operator(self) -> bool:
        return self.role == "operator"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TokenStore:
    def __init__(self, db: Database, known_merchants: set[str] | None = None):
        self.db = db
        self.known_merchants = known_merchants

    def issue(self, subject: str, role: str, merchant_id: str | None = None, ttl_hours: float = 24 * 7,
              label: str = "") -> tuple[str, dict]:
        """Create a credential. The plaintext token is returned ONCE and never persisted."""
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        if role == "merchant":
            if not merchant_id:
                raise ValueError("merchant tokens require merchant_id")
            if self.known_merchants is not None and merchant_id not in self.known_merchants:
                raise ValueError(f"unknown merchant_id: {merchant_id}")
        if role == "operator":
            merchant_id = None
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        h = hash_token(token)
        rec = {"token_hash": h, "token_id": h[:12], "subject": subject, "role": role, "merchant_id": merchant_id,
               "label": label, "created_at": _now().isoformat(),
               "expires_at": (_now() + timedelta(hours=ttl_hours)).isoformat()}
        with self.db.tx() as c:
            c.execute("INSERT INTO credentials (token_hash, token_id, subject, role, merchant_id, label, created_at, "
                      "expires_at) VALUES (:token_hash, :token_id, :subject, :role, :merchant_id, :label, :created_at, "
                      ":expires_at)", rec)
        public = {k: v for k, v in rec.items() if k != "token_hash"}
        return token, public

    def authenticate(self, token: str | None) -> Principal | None:
        if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 128:
            return None
        h = hash_token(token)
        row = self.db.one("SELECT * FROM credentials WHERE token_hash = ?", (h,))
        # constant-time confirmation (the indexed lookup is on a hash of a 256-bit secret)
        if not row or not hmac.compare_digest(row["token_hash"], h):
            return None
        if row["revoked_at"] or datetime.fromisoformat(row["expires_at"]) <= _now():
            return None
        return Principal(subject=row["subject"], role=row["role"], merchant_id=row["merchant_id"],
                         token_id=row["token_id"])

    def revoke(self, token_or_id: str) -> bool:
        key = hash_token(token_or_id) if token_or_id.startswith(TOKEN_PREFIX) else None
        with self.db.tx() as c:
            cur = c.execute("UPDATE credentials SET revoked_at = ? WHERE revoked_at IS NULL AND "
                            "(token_hash = ? OR token_id = ?)", (_now().isoformat(), key, token_or_id))
            return cur.rowcount > 0

    def list(self) -> list[dict]:
        return self.db.query("SELECT token_id, subject, role, merchant_id, label, created_at, expires_at, revoked_at "
                             "FROM credentials ORDER BY created_at")


_bearer = HTTPBearer(auto_error=False, description="Bearer token issued with `python -m scripts.issue_token`")


def _unauthorized(detail: str = "Missing or invalid credentials") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


async def get_principal(request: Request,
                        creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorized()
    principal = request.app.state.container.tokens.authenticate(creds.credentials)
    if principal is None:
        raise _unauthorized()
    return principal


async def require_merchant(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != "merchant":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Merchant credentials required")
    return principal


async def require_operator(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != "operator":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator credentials required")
    return principal
