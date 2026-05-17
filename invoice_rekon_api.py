"""
invoice_rekon_api.py — Customer payment reconciliation via GOWA WA data.

Flow:
  POST /api/rekon/sync          → parse new invoices + payment signals from SQLite
  GET  /api/rekon/invoices      → list parsed invoices with rekon status
  GET  /api/rekon/signals       → list payment signals (images/captions)
  POST /api/rekon/signals/{id}/ocr      → trigger vision OCR on one signal
  POST /api/rekon/match                 → manually link signal ↔ invoice
  POST /api/rekon/invoices/{id}/confirm → confirm match → create pemasukan_manual
  GET  /api/rekon/summary               → counts: unmatched invoices, signals, confirmed
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auth_api import COOKIE_NAME, _decode_token
from database import get_db_dict
from invoice_parser import (
    detect_payment_text,
    find_local_media,
    iter_invoice_messages,
    iter_payment_images,
    ocr_payment_image,
    parse_invoice_text,
)

router = APIRouter(prefix="/api/rekon", tags=["rekon"])


# ── Auth ────────────────────────────────────────────────────────────────────

def _require_auth(request: Request) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Session required")
    try:
        return _decode_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid session") from exc


# ── Pydantic models ─────────────────────────────────────────────────────────

class MatchRequest(BaseModel):
    signal_id: int
    invoice_rekon_id: int
    notes: Optional[str] = None


class ConfirmRequest(BaseModel):
    rekon_notes: Optional[str] = None
    create_pemasukan: bool = Field(default=True)


# ── DB helpers ──────────────────────────────────────────────────────────────

def _latest_synced_ts(conn) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("SELECT MAX(wa_timestamp) FROM invoice_rekon")
    row = cur.fetchone()
    val = row[0] if row else None
    return val  # already a string from DictRow date coercion


def _latest_signal_ts(conn) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("SELECT MAX(wa_timestamp) FROM payment_signal")
    row = cur.fetchone()
    return row[0] if row else None


def _upsert_invoice(conn, jid: str, phone: str, ts_str: str, msg: dict, parsed: dict) -> Optional[int]:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO invoice_rekon
            (nota_number, conversation_jid, phone, customer_name,
             total_tagihan, grand_total, status_invoice, layanan,
             terima_at, selesai_at, wa_timestamp, raw_message_text)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (nota_number) DO UPDATE
            SET status_invoice  = EXCLUDED.status_invoice,
                total_tagihan   = EXCLUDED.total_tagihan,
                updated_at      = NOW()
        RETURNING id
        """,
        (
            parsed["nota_number"],
            jid,
            phone,
            parsed["customer_name"],
            parsed["total_tagihan"],
            parsed["grand_total"],
            parsed["status_invoice"],
            parsed["layanan"],
            parsed["terima_at"],
            parsed["selesai_at"],
            ts_str,
            msg.get("message_text", "")[:4000],
        ),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def _upsert_signal(conn, jid: str, msg: dict, payload: dict, local_path: Optional[str]) -> Optional[int]:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO payment_signal
            (conversation_jid, wa_message_id, wa_timestamp, signal_type,
             is_payment, bank_name, amount, reference,
             raw_caption, local_file_path, confidence)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (wa_message_id) DO NOTHING
        RETURNING id
        """,
        (
            jid,
            msg.get("message_id"),
            msg["timestamp"],
            payload.get("signal_type", "text_caption"),
            payload.get("is_payment", False),
            payload.get("bank_name"),
            payload.get("amount"),
            payload.get("reference"),
            msg.get("message_text", "")[:500],
            local_path,
            payload.get("confidence", 0.0),
        ),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def _try_auto_match(conn, invoice_id: int, total: int, jid: str, inv_ts: str) -> None:
    """Auto-match a payment signal to an invoice if amount + jid + time window align."""
    cur = conn.cursor()
    # Window: signal sent within 24h AFTER invoice (customers pay after receiving invoice)
    cur.execute(
        """
        SELECT id, amount FROM payment_signal
        WHERE conversation_jid = %s
          AND is_payment = true
          AND amount = %s
          AND rekon_status = 'unmatched'
          AND wa_timestamp BETWEEN %s::timestamptz AND %s::timestamptz + INTERVAL '24 hours'
        ORDER BY wa_timestamp ASC
        LIMIT 1
        """,
        (jid, total, inv_ts, inv_ts),
    )
    signal = cur.fetchone()
    if not signal:
        return
    sig_id = signal["id"]
    cur.execute(
        """
        UPDATE payment_signal
        SET invoice_rekon_id = %s, rekon_status = 'auto_matched', rekon_at = NOW()
        WHERE id = %s
        """,
        (invoice_id, sig_id),
    )
    cur.execute(
        """
        UPDATE invoice_rekon
        SET payment_signal_id = %s, rekon_status = 'auto_matched', rekon_at = NOW()
        WHERE id = %s
        """,
        (sig_id, invoice_id),
    )


# ── Sync logic ──────────────────────────────────────────────────────────────

def _sync_invoices(conn, since_ts: Optional[str]) -> dict[str, int]:
    stats = {"invoices_new": 0, "invoices_updated": 0}
    for msg in iter_invoice_messages(since_ts=since_ts):
        parsed = parse_invoice_text(msg.get("message_text", ""))
        if not parsed:
            continue
        jid = msg["conversation_jid"]
        phone = jid.split("@")[0] if "@" in jid else jid
        inv_id = _upsert_invoice(conn, jid, phone, msg["timestamp"], msg, parsed)
        if inv_id:
            stats["invoices_new"] += 1
            _try_auto_match(conn, inv_id, parsed["total_tagihan"], jid, msg["timestamp"])
    return stats


def _sync_signals(conn, since_ts: Optional[str], jid_filter: Optional[set]) -> dict[str, int]:
    stats = {"signals_new": 0, "signals_skipped": 0}
    for msg in iter_payment_images(since_ts=since_ts, conversation_jids=jid_filter):
        caption = msg.get("message_text", "")
        msg_type = msg.get("message_type", "text")
        jid = msg["conversation_jid"]

        # Tier-1: check text caption
        payment = detect_payment_text(caption)
        if payment:
            payment["signal_type"] = "text_caption" if msg_type == "image" else "text_only"
        elif msg_type == "image" and not caption.startswith("[image:"):
            # caption present but no payment keyword — still queue for OCR later
            payment = {"is_payment": False, "signal_type": "text_caption", "confidence": 0.0}
        elif msg_type == "image":
            # No caption — file-only, queue for OCR
            payment = {"is_payment": False, "signal_type": "vision_ocr", "confidence": 0.0}
        else:
            stats["signals_skipped"] += 1
            continue

        local_path = find_local_media(msg["timestamp"]) if msg_type == "image" else None
        sig_id = _upsert_signal(conn, jid, msg, payment, local_path)
        if sig_id:
            stats["signals_new"] += 1
            if payment.get("is_payment") and payment.get("amount"):
                # Try to find matching invoice
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, total_tagihan, wa_timestamp FROM invoice_rekon
                    WHERE conversation_jid = %s
                      AND total_tagihan = %s
                      AND rekon_status = 'unmatched'
                      AND wa_timestamp <= %s::timestamptz
                    ORDER BY wa_timestamp DESC LIMIT 1
                    """,
                    (jid, payment["amount"], msg["timestamp"]),
                )
                inv = cur.fetchone()
                if inv:
                    cur.execute(
                        """
                        UPDATE payment_signal
                        SET invoice_rekon_id = %s, rekon_status = 'auto_matched', rekon_at = NOW()
                        WHERE id = %s
                        """,
                        (inv["id"], sig_id),
                    )
                    cur.execute(
                        """
                        UPDATE invoice_rekon
                        SET payment_signal_id = %s, rekon_status = 'auto_matched', rekon_at = NOW(),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (sig_id, inv["id"]),
                    )
    return stats


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_rekon(
    full: bool = False,
    _: dict[str, Any] = Depends(_require_auth),
):
    """
    Parse new FAKTUR ELEKTRONIK messages and payment signals from GOWA SQLite.
    Pass ?full=true to re-process all history (idempotent via ON CONFLICT).
    """
    with get_db_dict() as conn:
        since_inv = None if full else _latest_synced_ts(conn)
        since_sig = None if full else _latest_signal_ts(conn)

        # Get all JIDs that have invoices (to limit signal search scope)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT conversation_jid FROM invoice_rekon")
        known_jids = {r["conversation_jid"] for r in cur.fetchall()}

        inv_stats  = _sync_invoices(conn, since_inv)

        # Refresh known JIDs after new invoices
        cur.execute("SELECT DISTINCT conversation_jid FROM invoice_rekon")
        all_jids = {r["conversation_jid"] for r in cur.fetchall()}

        sig_stats  = _sync_signals(conn, since_sig, all_jids if all_jids else None)

    return {"ok": True, **inv_stats, **sig_stats}


@router.get("/summary")
async def rekon_summary(_: dict[str, Any] = Depends(_require_auth)):
    with get_db_dict() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE rekon_status = 'unmatched')   AS invoices_unmatched,
                COUNT(*) FILTER (WHERE rekon_status = 'auto_matched') AS invoices_auto,
                COUNT(*) FILTER (WHERE rekon_status = 'confirmed')    AS invoices_confirmed,
                COALESCE(SUM(total_tagihan) FILTER (WHERE rekon_status = 'confirmed'), 0) AS total_confirmed,
                COALESCE(SUM(total_tagihan) FILTER (WHERE rekon_status = 'unmatched'), 0) AS total_unmatched
            FROM invoice_rekon
        """)
        inv = cur.fetchone()
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE rekon_status = 'unmatched' AND is_payment = true)  AS signals_unmatched,
                COUNT(*) FILTER (WHERE rekon_status = 'auto_matched')                     AS signals_auto,
                COUNT(*) FILTER (WHERE local_file_path IS NOT NULL AND is_payment = false
                                  AND signal_type = 'vision_ocr')                          AS pending_ocr
            FROM payment_signal
        """)
        sig = cur.fetchone()
    return {
        "invoices": {
            "unmatched":  inv["invoices_unmatched"],
            "auto_matched": inv["invoices_auto"],
            "confirmed":  inv["invoices_confirmed"],
            "total_confirmed_rp": inv["total_confirmed"],
            "total_unmatched_rp": inv["total_unmatched"],
        },
        "signals": {
            "unmatched":    sig["signals_unmatched"],
            "auto_matched": sig["signals_auto"],
            "pending_ocr":  sig["pending_ocr"],
        },
    }


@router.get("/invoices")
async def list_invoices(
    status:  Optional[str] = None,
    days:    int = Query(default=30, ge=1, le=365),
    limit:   int = Query(default=50, ge=1, le=200),
    offset:  int = Query(default=0, ge=0),
    _: dict[str, Any] = Depends(_require_auth),
):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    where_parts = ["wa_timestamp >= %s"]
    params: list[Any] = [since]
    if status:
        where_parts.append("rekon_status = %s")
        params.append(status)
    with get_db_dict() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT i.*, ps.bank_name, ps.amount AS signal_amount, ps.reference, ps.signal_type
            FROM invoice_rekon i
            LEFT JOIN payment_signal ps ON ps.id = i.payment_signal_id
            WHERE {' AND '.join(where_parts)}
            ORDER BY i.wa_timestamp DESC
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        items = cur.fetchall()
        cur.execute(
            f"SELECT COUNT(*) AS n FROM invoice_rekon WHERE {' AND '.join(where_parts)}",
            params,
        )
        total = cur.fetchone()["n"]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/signals")
async def list_signals(
    status:       Optional[str] = None,
    is_payment:   Optional[bool] = None,
    pending_ocr:  bool = False,
    days:         int = Query(default=30, ge=1, le=365),
    limit:        int = Query(default=50, ge=1, le=200),
    offset:       int = Query(default=0, ge=0),
    _: dict[str, Any] = Depends(_require_auth),
):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    where_parts = ["wa_timestamp >= %s"]
    params: list[Any] = [since]
    if status:
        where_parts.append("rekon_status = %s")
        params.append(status)
    if is_payment is not None:
        where_parts.append("is_payment = %s")
        params.append(is_payment)
    if pending_ocr:
        where_parts.append("signal_type = 'vision_ocr' AND is_payment = false AND local_file_path IS NOT NULL")
    with get_db_dict() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT ps.*, ir.nota_number, ir.customer_name, ir.total_tagihan
            FROM payment_signal ps
            LEFT JOIN invoice_rekon ir ON ir.id = ps.invoice_rekon_id
            WHERE {' AND '.join(where_parts)}
            ORDER BY ps.wa_timestamp DESC
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        items = cur.fetchall()
    return {"items": items, "limit": limit, "offset": offset}


@router.post("/signals/{signal_id}/ocr")
async def run_ocr(
    signal_id: int,
    background_tasks: BackgroundTasks,
    _: dict[str, Any] = Depends(_require_auth),
):
    """Trigger gpt-4o-mini vision OCR on a single payment signal image."""
    with get_db_dict() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, local_file_path FROM payment_signal WHERE id = %s", (signal_id,))
        sig = cur.fetchone()
        if not sig:
            raise HTTPException(status_code=404, detail="Signal tidak ditemukan")
        if not sig["local_file_path"]:
            raise HTTPException(status_code=400, detail="Signal tidak punya file lokal")

    async def _do_ocr():
        result = await ocr_payment_image(sig["local_file_path"])
        with get_db_dict() as conn2:
            cur2 = conn2.cursor()
            import json as _json
            cur2.execute(
                """
                UPDATE payment_signal
                SET is_payment  = %s,
                    bank_name   = %s,
                    amount      = %s,
                    payment_datetime = %s,
                    reference   = %s,
                    recipient   = %s,
                    ocr_raw     = %s,
                    confidence  = %s,
                    signal_type = 'vision_ocr'
                WHERE id = %s
                """,
                (
                    result.get("is_payment", False),
                    result.get("bank_name"),
                    result.get("amount"),
                    result.get("datetime"),
                    result.get("reference"),
                    result.get("recipient"),
                    _json.dumps(result),
                    0.9 if result.get("is_payment") else 0.1,
                    signal_id,
                ),
            )
            # Try auto-match after OCR
            if result.get("is_payment") and result.get("amount"):
                cur2.execute(
                    "SELECT conversation_jid, wa_timestamp FROM payment_signal WHERE id = %s",
                    (signal_id,),
                )
                row = cur2.fetchone()
                if row:
                    cur2.execute(
                        """
                        SELECT id FROM invoice_rekon
                        WHERE conversation_jid = %s
                          AND total_tagihan = %s
                          AND rekon_status = 'unmatched'
                          AND wa_timestamp <= %s::timestamptz
                        ORDER BY wa_timestamp DESC LIMIT 1
                        """,
                        (row["conversation_jid"], result["amount"], row["wa_timestamp"]),
                    )
                    inv = cur2.fetchone()
                    if inv:
                        cur2.execute(
                            "UPDATE payment_signal SET invoice_rekon_id=%s, rekon_status='auto_matched', rekon_at=NOW() WHERE id=%s",
                            (inv["id"], signal_id),
                        )
                        cur2.execute(
                            "UPDATE invoice_rekon SET payment_signal_id=%s, rekon_status='auto_matched', rekon_at=NOW(), updated_at=NOW() WHERE id=%s",
                            (signal_id, inv["id"]),
                        )

    background_tasks.add_task(_do_ocr)
    return {"ok": True, "signal_id": signal_id, "status": "ocr_queued"}


@router.post("/signals/ocr-batch")
async def run_ocr_batch(
    background_tasks: BackgroundTasks,
    limit: int = Query(default=20, ge=1, le=100),
    _: dict[str, Any] = Depends(_require_auth),
):
    """Queue OCR for up to `limit` unprocessed image signals."""
    with get_db_dict() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id FROM payment_signal
            WHERE signal_type = 'vision_ocr'
              AND is_payment = false
              AND local_file_path IS NOT NULL
            ORDER BY wa_timestamp DESC
            LIMIT %s
            """,
            (limit,),
        )
        ids = [r["id"] for r in cur.fetchall()]

    async def _batch():
        for sig_id in ids:
            with get_db_dict() as conn2:
                cur2 = conn2.cursor()
                cur2.execute("SELECT local_file_path FROM payment_signal WHERE id = %s", (sig_id,))
                row = cur2.fetchone()
                if not row or not row["local_file_path"]:
                    continue
            result = await ocr_payment_image(row["local_file_path"])
            import json as _json
            with get_db_dict() as conn3:
                cur3 = conn3.cursor()
                cur3.execute(
                    """
                    UPDATE payment_signal SET
                        is_payment = %s, bank_name = %s, amount = %s,
                        payment_datetime = %s, reference = %s, recipient = %s,
                        ocr_raw = %s, confidence = %s
                    WHERE id = %s
                    """,
                    (
                        result.get("is_payment", False), result.get("bank_name"),
                        result.get("amount"), result.get("datetime"),
                        result.get("reference"), result.get("recipient"),
                        _json.dumps(result),
                        0.9 if result.get("is_payment") else 0.1,
                        sig_id,
                    ),
                )
            await asyncio.sleep(0.5)   # gentle rate-limit

    background_tasks.add_task(_batch)
    return {"ok": True, "queued": len(ids)}


@router.post("/match")
async def manual_match(
    data: MatchRequest,
    _: dict[str, Any] = Depends(_require_auth),
):
    with get_db_dict() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM payment_signal WHERE id = %s", (data.signal_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Signal tidak ditemukan")
        cur.execute("SELECT id FROM invoice_rekon WHERE id = %s", (data.invoice_rekon_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Invoice tidak ditemukan")
        cur.execute(
            """
            UPDATE payment_signal
            SET invoice_rekon_id = %s, rekon_status = 'manual_matched',
                rekon_at = NOW()
            WHERE id = %s
            """,
            (data.invoice_rekon_id, data.signal_id),
        )
        cur.execute(
            """
            UPDATE invoice_rekon
            SET payment_signal_id = %s, rekon_status = 'manual_matched',
                rekon_at = NOW(), rekon_notes = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (data.signal_id, data.notes, data.invoice_rekon_id),
        )
    return {"ok": True, "signal_id": data.signal_id, "invoice_id": data.invoice_rekon_id}


@router.post("/invoices/{invoice_id}/confirm")
async def confirm_invoice_paid(
    invoice_id: int,
    data: ConfirmRequest,
    session: dict[str, Any] = Depends(_require_auth),
):
    """
    Confirm a matched invoice as paid.
    Optionally creates a pemasukan_manual record so it appears in P&L.
    """
    with get_db_dict() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM invoice_rekon WHERE id = %s",
            (invoice_id,),
        )
        inv = cur.fetchone()
        if not inv:
            raise HTTPException(status_code=404, detail="Invoice tidak ditemukan")
        if inv["rekon_status"] not in ("auto_matched", "manual_matched"):
            raise HTTPException(
                status_code=400,
                detail=f"Invoice harus di-match dulu (status saat ini: {inv['rekon_status']})",
            )

        pemasukan_id = None
        if data.create_pemasukan:
            # Build payment method from signal if available
            metode = "transfer"
            if inv["payment_signal_id"]:
                cur.execute(
                    "SELECT bank_name FROM payment_signal WHERE id = %s",
                    (inv["payment_signal_id"],),
                )
                sig = cur.fetchone()
                if sig and sig["bank_name"]:
                    bank = sig["bank_name"].lower()
                    if any(x in bank for x in ["gopay", "ovo", "dana", "shopee", "qris"]):
                        metode = "qris"
                    elif "cash" in bank:
                        metode = "cash"

            cur.execute(
                """
                INSERT INTO pemasukan_manual
                    (tanggal, nama_customer, layanan, nominal, metode_bayar,
                     dicatat_oleh, status, catatan)
                VALUES (%s, %s, %s, %s, %s, %s, 'verified', %s)
                RETURNING id
                """,
                (
                    inv["wa_timestamp"],
                    inv["customer_name"],
                    inv["layanan"],
                    inv["total_tagihan"],
                    metode,
                    session.get("sub", "rekon"),
                    f"Nota {inv['nota_number']} | {data.rekon_notes or ''}".strip(" |"),
                ),
            )
            pm_row = cur.fetchone()
            pemasukan_id = pm_row["id"] if pm_row else None

        cur.execute(
            """
            UPDATE invoice_rekon
            SET rekon_status = 'confirmed',
                status_invoice = 'lunas',
                pemasukan_manual_id = %s,
                rekon_notes = %s,
                rekon_at = NOW(),
                updated_at = NOW()
            WHERE id = %s
            """,
            (pemasukan_id, data.rekon_notes, invoice_id),
        )

    return {
        "ok": True,
        "invoice_id": invoice_id,
        "pemasukan_manual_id": pemasukan_id,
    }


@router.delete("/invoices/{invoice_id}/unmatch")
async def unmatch_invoice(
    invoice_id: int,
    _: dict[str, Any] = Depends(_require_auth),
):
    with get_db_dict() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT payment_signal_id FROM invoice_rekon WHERE id = %s",
            (invoice_id,),
        )
        inv = cur.fetchone()
        if not inv:
            raise HTTPException(status_code=404, detail="Invoice tidak ditemukan")
        sig_id = inv["payment_signal_id"]
        cur.execute(
            "UPDATE invoice_rekon SET payment_signal_id=NULL, rekon_status='unmatched', rekon_at=NULL, updated_at=NOW() WHERE id=%s",
            (invoice_id,),
        )
        if sig_id:
            cur.execute(
                "UPDATE payment_signal SET invoice_rekon_id=NULL, rekon_status='unmatched', rekon_at=NULL WHERE id=%s",
                (sig_id,),
            )
    return {"ok": True}
