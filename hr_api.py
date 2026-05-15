"""
SIJI Bintaro — HR / Lamaran API
ATS (Applicant Tracking System) dengan cookie auth
Pipeline: baru → dihubungi → interview → diterima | ditolak/arsip
"""

from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date
import asyncio
import asyncpg
import jwt as pyjwt
import os

router = APIRouter(prefix="/api/hr", tags=["hr"])

# ─── Config ──────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get(
    "SIJI_DB_URL",
    "postgresql://livin:L1v1n!B1nt4r0_2026@127.0.0.1:5432/livininbintaro",
)
_jwt_secret = os.environ.get("JWT_SECRET")
if not _jwt_secret:
    raise RuntimeError("JWT_SECRET env var is required but not set")
JWT_SECRET: str = _jwt_secret
COOKIE_NAME = "siji_session"

VALID_STATUSES = ["baru", "imported", "review", "dihubungi", "interview",
                  "diterima", "ditolak", "arsip", "tidak_aktif"]

# ─── Connection pool (lazy singleton) ────────────────────────────────────────

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await asyncpg.create_pool(
                    DATABASE_URL,
                    min_size=2,
                    max_size=10,
                    command_timeout=30,
                    server_settings={"search_path": "siji_bintaro,public"},
                )
    return _pool


# ─── Auth helper ─────────────────────────────────────────────────────────────

def _require_auth(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Login required")
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session")


# ─── Models ──────────────────────────────────────────────────────────────────

class PipelineUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    tgl_dihubungi: Optional[str] = None
    tgl_interview: Optional[str] = None
    callback_interest: Optional[bool] = None


class SendWA(BaseModel):
    message: str


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fmt_dates(d: dict) -> dict:
    for k in ("created_at", "tgl_dihubungi", "tgl_interview", "tgl_update", "tgl_bergabung"):
        if d.get(k):
            v = d[k]
            d[k] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)
    return d


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/lamaran")
async def list_lamaran(
    request: Request,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    _require_auth(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        where = ["TRUE"]
        params: list = []
        i = 1

        if status and status != "semua":
            if status == "arsip":
                where.append("l.status IN ('ditolak', 'arsip', 'tidak_aktif')")
            elif status == "aktif":
                where.append("l.status NOT IN ('diterima', 'ditolak', 'arsip', 'tidak_aktif')")
            else:
                where.append(f"l.status = ${i}")
                params.append(status)
                i += 1

        if search:
            where.append(f"(l.nama ILIKE ${i} OR l.whatsapp ILIKE ${i} OR l.posisi ILIKE ${i})")
            params.append(f"%{search}%")
            i += 1

        where_clause = " AND ".join(where)
        offset = (page - 1) * limit

        count_row = await conn.fetchval(
            f"SELECT COUNT(*) FROM siji_bintaro.lamaran l WHERE {where_clause}", *params
        )

        query = f"""
            SELECT l.*,
                   k.id as karyawan_id, k.status_kerja
            FROM siji_bintaro.lamaran l
            LEFT JOIN siji_bintaro.karyawan k ON k.lamaran_id = l.id
            WHERE {where_clause}
            ORDER BY
                CASE l.status
                    WHEN 'baru' THEN 1
                    WHEN 'imported' THEN 2
                    WHEN 'dihubungi' THEN 3
                    WHEN 'interview' THEN 4
                    WHEN 'diterima' THEN 5
                    WHEN 'ditolak' THEN 6
                    WHEN 'arsip' THEN 7
                    WHEN 'tidak_aktif' THEN 8
                    ELSE 9
                END,
                l.created_at DESC
            LIMIT ${i} OFFSET ${i+1}
        """
        rows = await conn.fetch(query, *params, limit, offset)

        return {
            "data": [_fmt_dates(dict(r)) for r in rows],
            "total": count_row,
            "page": page,
            "pages": max(1, -(-count_row // limit)),
        }


@router.get("/lamaran/stats")
async def lamaran_stats(request: Request):
    _require_auth(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, COUNT(*) as cnt FROM siji_bintaro.lamaran GROUP BY status"
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM siji_bintaro.lamaran")
        stats = {s: 0 for s in VALID_STATUSES}
        stats["total"] = total
        for r in rows:
            stats[r["status"]] = r["cnt"]
        stats["arsip_total"] = (
            stats.get("ditolak", 0) + stats.get("arsip", 0) + stats.get("tidak_aktif", 0)
        )
        stats["callback_pool"] = await conn.fetchval(
            "SELECT COUNT(*) FROM siji_bintaro.lamaran WHERE callback_interest = TRUE"
        )
        return stats


@router.get("/lamaran/{lid}")
async def get_lamaran(lid: int, request: Request):
    _require_auth(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT l.*, k.id as karyawan_id, k.status_kerja, k.tgl_bergabung
            FROM siji_bintaro.lamaran l
            LEFT JOIN siji_bintaro.karyawan k ON k.lamaran_id = l.id
            WHERE l.id = $1
        """, lid)
        if not row:
            raise HTTPException(status_code=404)
        return _fmt_dates(dict(row))


@router.patch("/lamaran/{lid}")
async def update_lamaran(lid: int, body: PipelineUpdate, request: Request):
    _require_auth(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM siji_bintaro.lamaran WHERE id = $1", lid
        )
        if not existing:
            raise HTTPException(status_code=404)

        now = datetime.now()
        fields = ["tgl_update = $1"]
        params: list = [now]
        i = 2

        if body.status is not None:
            if body.status not in VALID_STATUSES:
                raise HTTPException(
                    status_code=400, detail=f"Status tidak valid: {body.status}"
                )
            fields.append(f"status = ${i}"); params.append(body.status); i += 1
            if body.status == "dihubungi" and not existing["tgl_dihubungi"]:
                fields.append(f"tgl_dihubungi = ${i}"); params.append(now); i += 1
            if body.status == "interview" and not existing["tgl_interview"]:
                fields.append(f"tgl_interview = ${i}"); params.append(now); i += 1

        if body.notes is not None:
            fields.append(f"notes = ${i}"); params.append(body.notes); i += 1
        if body.tgl_dihubungi is not None:
            fields.append(f"tgl_dihubungi = ${i}"); params.append(body.tgl_dihubungi); i += 1
        if body.tgl_interview is not None:
            fields.append(f"tgl_interview = ${i}"); params.append(body.tgl_interview); i += 1
        if body.callback_interest is not None:
            fields.append(f"callback_interest = ${i}"); params.append(body.callback_interest); i += 1

        params.append(lid)
        await conn.execute(
            f"UPDATE siji_bintaro.lamaran SET {', '.join(fields)} WHERE id = ${i}",
            *params,
        )

        if body.status == "diterima":
            existing_k = await conn.fetchrow(
                "SELECT id FROM siji_bintaro.karyawan WHERE lamaran_id = $1", lid
            )
            if not existing_k:
                await conn.execute("""
                    INSERT INTO siji_bintaro.karyawan (lamaran_id, nama, whatsapp, posisi, tgl_bergabung)
                    VALUES ($1, $2, $3, $4, $5)
                """, lid, existing["nama"], existing["whatsapp"], existing["posisi"], date.today())

        updated = await conn.fetchrow(
            "SELECT * FROM siji_bintaro.lamaran WHERE id = $1", lid
        )
        return {"ok": True, "data": _fmt_dates(dict(updated))}


@router.delete("/lamaran/{lid}/archive")
async def archive_lamaran(lid: int, request: Request, callback: bool = False):
    """Archive applicant — optionally mark as callback opportunity"""
    _require_auth(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE siji_bintaro.lamaran
            SET status = 'arsip', callback_interest = $1, tgl_update = $2
            WHERE id = $3
        """, callback, datetime.now(), lid)
        if result == "UPDATE 0":
            raise HTTPException(status_code=404)
        return {"ok": True, "archived": True, "callback": callback}


@router.post("/lamaran/{lid}/send-wa")
async def send_wa(lid: int, body: SendWA, request: Request):
    _require_auth(request)
    import httpx
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM siji_bintaro.lamaran WHERE id = $1", lid
        )
        if not row:
            raise HTTPException(status_code=404)

        wa = row["whatsapp"].replace("+", "").replace("-", "").replace(" ", "")
        if wa.startswith("0"):
            wa = "62" + wa[1:]

        fonnte_token = os.environ.get("FONNTE_TOKEN", "")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.fonnte.com/send",
                headers={"Authorization": fonnte_token},
                data={"target": wa, "message": body.message, "countryCode": "62"},
                timeout=15,
            )
        ok = resp.status_code == 200

        if ok and not row["tgl_dihubungi"]:
            await conn.execute("""
                UPDATE siji_bintaro.lamaran
                SET tgl_dihubungi = $1,
                    status = CASE WHEN status IN ('baru','imported','review') THEN 'dihubungi' ELSE status END,
                    tgl_update = $1
                WHERE id = $2
            """, datetime.now(), lid)

        return {"ok": ok, "wa_number": wa, "detail": resp.json() if ok else resp.text}
