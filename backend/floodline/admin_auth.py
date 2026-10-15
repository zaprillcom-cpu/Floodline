"""Token check for the Valencia load endpoint. The token is an env var, never a default."""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException

from floodline.config import load_dotenv


def require_admin(authorization: str | None, x_admin_token: str | None) -> None:
    """Accept ``Authorization: Bearer`` or ``X-Admin-Token``. 503 if unset, 401 if wrong."""
    load_dotenv()
    expected = os.environ.get("ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
    presented = ""
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer":
            presented = token.strip()
    if not presented and x_admin_token:
        presented = x_admin_token.strip()
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="Invalid admin token")
