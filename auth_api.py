"""
SIJI Bintaro — Unified Auth API
Cookie-based JWT SSO untuk semua subdomain .sijibintaro.id
"""

from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from passlib.apache import HtpasswdFile
import jwt as pyjwt
import os
import time
import secrets

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ─── Config ──────────────────────────────────────────────────────────────────
HTPASSWD_FILE = "/etc/nginx/.siji-unified-htpasswd"
JWT_SECRET = os.environ.get("JWT_SECRET") or "siji-jwt-secret-2026-bintaro-xK9mP2qR"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 7
COOKIE_NAME = "siji_session"
COOKIE_DOMAIN = ".sijibintaro.id"

# Role mapping per user
USER_ROLES = {
    "siji-admin": "admin",
    "siji": "staff",
    "ocha": "staff",
    "filean": "staff",
}


def _load_htpasswd():
    return HtpasswdFile(HTPASSWD_FILE)


def _verify_password(username: str, password: str) -> bool:
    try:
        ht = _load_htpasswd()
        return ht.check_password(username, password)
    except Exception:
        return False


def _make_token(username: str) -> str:
    payload = {
        "sub": username,
        "role": USER_ROLES.get(username, "staff"),
        "exp": int(time.time()) + JWT_EXPIRE_DAYS * 86400,
        "iat": int(time.time()),
        "jti": secrets.token_hex(8),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    return pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ─── Endpoints ───────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    if not _verify_password(body.username, body.password):
        raise HTTPException(status_code=401, detail="Username atau password salah")

    token = _make_token(body.username)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        domain=COOKIE_DOMAIN,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=JWT_EXPIRE_DAYS * 86400,
        path="/",
    )
    return {
        "ok": True,
        "username": body.username,
        "role": USER_ROLES.get(body.username, "staff"),
    }


@router.get("/verify")
async def verify(request: Request):
    """Internal endpoint — dipanggil nginx auth_request"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="No session")
    try:
        payload = _decode_token(token)
        return JSONResponse(
            status_code=200,
            content={"ok": True, "user": payload["sub"]},
            headers={
                "X-Auth-User": payload["sub"],
                "X-Auth-Role": payload.get("role", "staff"),
            }
        )
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session")


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(
        key=COOKIE_NAME,
        domain=COOKIE_DOMAIN,
        path="/",
    )
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401)
    try:
        payload = _decode_token(token)
        return {
            "username": payload["sub"],
            "role": payload.get("role", "staff"),
            "exp": payload["exp"],
        }
    except Exception:
        raise HTTPException(status_code=401)
