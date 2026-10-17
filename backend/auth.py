"""Login: verify Auth0 access tokens (RS256-signed JWTs) and map each one to a row in app_users.

    AUTH0_DOMAIN     your tenant, e.g. planet-control.us.auth0.com
    AUTH0_AUDIENCE   the API identifier you created in Auth0, e.g. https://api.planet-control

Every endpoint that writes depends on `current_user`. A token is trusted only after its signature checks out against
Auth0's published keys and its issuer, audience and expiry are right. Passwords never reach this server: Auth0 holds
them. If Auth0 isn't configured, writes are refused (503), except with PLANIT_DEV=1 on a laptop, where a local
"dev" user is used.
"""
import os
import time
from collections import defaultdict, deque

import httpx
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.db import connection

bearer = HTTPBearer(auto_error=False)
_jwks = {}
DEV_SUB = "dev|local"


def domain():
    return os.environ.get("AUTH0_DOMAIN", "").removeprefix("https://").rstrip("/")


def verify(token):
    """The token's claims, or 401. Signing keys are fetched from the tenant once and cached."""
    d = domain()
    if d not in _jwks:
        _jwks[d] = jwt.PyJWKClient(f"https://{d}/.well-known/jwks.json", cache_keys=True, lifespan=3600)
    try:
        key = _jwks[d].get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256"], audience=os.environ.get("AUTH0_AUDIENCE"),
                          issuer=f"https://{d}/", leeway=30, options={"require": ["exp", "iat", "sub"]})
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Invalid sign-in token ({type(e).__name__}). Sign in again.",
                            headers={"WWW-Authenticate": "Bearer"})


def profile(token):
    """Name and email from Auth0's /userinfo (verified by Auth0, unlike anything the browser could send us)."""
    try:
        r = httpx.get(f"https://{domain()}/userinfo", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        r.raise_for_status()
        p = r.json()
        return p.get("name") or p.get("nickname"), p.get("email")
    except (httpx.HTTPError, ValueError):
        return None, None


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)):
    """The signed-in user (created on first sight), or 401/503."""
    token = None
    if domain():
        if creds is None:
            raise HTTPException(401, "Sign in first.", headers={"WWW-Authenticate": "Bearer"})
        token = creds.credentials
        sub = verify(token)["sub"]
    elif os.environ.get("PLANIT_DEV") == "1":
        sub = DEV_SUB
    else:
        raise HTTPException(503, "Sign-in isn't configured on this server (set AUTH0_DOMAIN and AUTH0_AUDIENCE).")
    with connection() as conn:
        user = conn.execute("INSERT INTO app_users (auth0_sub) VALUES (%s) ON CONFLICT (auth0_sub) "
                            "DO UPDATE SET last_seen_at = now() RETURNING *", (sub,)).fetchone()
        if user["name"] is None:
            name, email = profile(token) if token else ("Dev user", None)
            if name or email:
                user = conn.execute("UPDATE app_users SET name = %s, email = %s WHERE user_id = %s RETURNING *",
                                    (name, email, user["user_id"])).fetchone()
    return user


# ponytail: in-memory and per process, so limits reset on restart and multiply with replicas; move to Redis or
# a gateway if the API ever runs on more than one machine.
_hits = defaultdict(deque)


def rate_limit(name, per_minute):
    """Dependency: the signed-in user, or 429 after `per_minute` calls to this group in the last minute."""
    def check(user=Depends(current_user)):
        q, now = _hits[(name, user["user_id"])], time.monotonic()
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= per_minute:
            raise HTTPException(429, "Too many requests; try again in a minute.",
                                headers={"Retry-After": str(int(61 - (now - q[0])))})
        q.append(now)
        return user
    return check
