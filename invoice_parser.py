"""
invoice_parser.py — Parse FAKTUR ELEKTRONIK WA messages and detect payment signals.

Tier-1 (free):  regex on message_text / caption
Tier-2 ($0.00047/img):  gpt-4o-mini vision OCR on local .jpe files
"""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

# ── Config ─────────────────────────────────────────────────────────────────
GOWA_DB       = "/opt/siji-dashboard/siji_database.db"
MEDIA_DIR     = "/opt/gowa/storages"

# Vision OCR — OpenAI gpt-4o-mini (only model confirmed to work, ~$0.00047/img)
OPENAI_KEY    = os.getenv("OPENAI_API_KEY", "")
OPENAI_URL    = "https://api.openai.com/v1/chat/completions"
OCR_MODEL     = "gpt-4o-mini"

# Text fallback — DeepSeek v4-flash (no vision, used when OpenAI unavailable)
# Handles: ambiguous captions, structured text extraction, caption classification
DEEPSEEK_KEY  = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL  = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"

# ── Regex patterns ──────────────────────────────────────────────────────────

_RE_NOTA     = re.compile(r"Nomor Nota\s*[:\-]\s*\*?(\w+)\*?", re.I)
_RE_PELANGGAN = re.compile(r"Pelanggan Yth\s*[:\-]\s*\n?(.+)", re.I)
_RE_TAGIHAN  = re.compile(r"Total tagihan\s*[:\-]\s*Rp\s*([\d.,]+)", re.I)
_RE_GRAND    = re.compile(r"Grand total\s*[:\-]\s*Rp\s*([\d.,]+)", re.I)
_RE_STATUS   = re.compile(r"Status\s*[:\-]\s*([\w ]+)", re.I)
_RE_TERIMA   = re.compile(r"Terima\s*[:\-]\s*(\d{2}/\d{2}/\d{4}(?:\s+\d{2}:\d{2})?)", re.I)
_RE_SELESAI  = re.compile(r"Selesai\s*[:\-]\s*(\d{2}/\d{2}/\d{4}(?:\s+\d{2}:\d{2})?)", re.I)
_RE_LAYANAN  = re.compile(r"✅\s+(.+?)(?:\n|,|\d+\s*KG|\d+\s*PCS)", re.I)

# Payment text signals (Tier-1 captions / text messages)
_RE_PAY_AMOUNT  = re.compile(r"(?:Rp|rp)\s*([\d.,]+)", re.I)
_RE_PAY_BANK    = re.compile(
    r"\b(SeaBank|BCA|BNI|BRI|Mandiri|GoPay|OVO|Dana|ShopeePay|Jago|Jenius|BSI|Permata|CIMB|Niaga|Sakuku|LinkAja|Flip)\b",
    re.I,
)
_RE_PAY_KW      = re.compile(
    r"\b(transfer|bayar|kirim|mengirimkan|pembayaran|konfirmasi|lunas|selesai bayar|sudah transfer|berhasil)\b",
    re.I,
)
_RE_PAY_REF     = re.compile(r"\b(\d{15,25})\b")

_DATE_FMTS = ["%d/%m/%Y %H:%M", "%d/%m/%Y"]


def _clean_rp(raw: str) -> int:
    return int(re.sub(r"[.,]", "", raw.strip()))


def _parse_dt(raw: str) -> Optional[datetime]:
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


# ── Invoice parser ───────────────────────────────────────────────────────────

def parse_invoice_text(text: str) -> Optional[dict[str, Any]]:
    """Return structured dict from a FAKTUR ELEKTRONIK message, or None."""
    if "FAKTUR ELEKTRONIK" not in text:
        return None
    nota_m      = _RE_NOTA.search(text)
    pelanggan_m = _RE_PELANGGAN.search(text)
    tagihan_m   = _RE_TAGIHAN.search(text)
    if not (nota_m and tagihan_m):
        return None

    grand_m   = _RE_GRAND.search(text)
    status_m  = _RE_STATUS.search(text)
    terima_m  = _RE_TERIMA.search(text)
    selesai_m = _RE_SELESAI.search(text)
    layanan_m = _RE_LAYANAN.search(text)

    status_raw = (status_m.group(1) if status_m else "").strip().lower()
    if "lunas" in status_raw and "belum" not in status_raw:
        status_invoice = "lunas"
    elif "belum" in status_raw:
        status_invoice = "belum_lunas"
    else:
        status_invoice = "unknown"

    return {
        "nota_number":    nota_m.group(1).strip(),
        "customer_name":  pelanggan_m.group(1).strip() if pelanggan_m else None,
        "total_tagihan":  _clean_rp(tagihan_m.group(1)),
        "grand_total":    _clean_rp(grand_m.group(1)) if grand_m else None,
        "status_invoice": status_invoice,
        "layanan":        layanan_m.group(1).strip() if layanan_m else None,
        "terima_at":      _parse_dt(terima_m.group(1)) if terima_m else None,
        "selesai_at":     _parse_dt(selesai_m.group(1)) if selesai_m else None,
    }


# ── Payment signal (Tier-1 text) ─────────────────────────────────────────────

def detect_payment_text(caption: str) -> Optional[dict[str, Any]]:
    """
    Detect payment info from a text caption.
    Returns a dict with payment fields if this looks like a payment, else None.
    """
    if not caption or not _RE_PAY_KW.search(caption):
        return None
    amount_m = _RE_PAY_AMOUNT.search(caption)
    if not amount_m:
        return None
    bank_m = _RE_PAY_BANK.search(caption)
    ref_m  = _RE_PAY_REF.search(caption)
    return {
        "is_payment": True,
        "bank_name":  bank_m.group(1) if bank_m else None,
        "amount":     _clean_rp(amount_m.group(1)),
        "reference":  ref_m.group(0) if ref_m else None,
        "confidence": 0.75,
    }


# ── Local file finder ─────────────────────────────────────────────────────────

def find_local_media(wa_timestamp_str: str) -> Optional[str]:
    """
    Map a wa_messages.timestamp (ISO 8601) to the nearest local GOWA .jpe file.
    Files are named {unix_ts}-{uuid}.jpe — we match within 60 seconds.
    """
    try:
        dt = datetime.fromisoformat(wa_timestamp_str.replace("Z", "+00:00"))
        target_ts = int(dt.timestamp())
    except Exception:
        return None

    best_diff, best_path = 61, None
    try:
        for fname in os.listdir(MEDIA_DIR):
            if not fname.endswith(".jpe"):
                continue
            parts = fname.split("-", 1)
            if not parts[0].isdigit():
                continue
            diff = abs(int(parts[0]) - target_ts)
            if diff < best_diff:
                best_diff = diff
                best_path = os.path.join(MEDIA_DIR, fname)
    except OSError:
        pass
    return best_path


# ── Text-fallback payment classifier (DeepSeek, Tier-1.5) ────────────────────

async def classify_payment_caption(caption: str) -> dict[str, Any]:
    """
    Use DeepSeek v4-flash to classify an ambiguous text caption as payment proof.
    Called when regex Tier-1 is inconclusive but a keyword hint exists.
    Falls back gracefully if DeepSeek key is absent.
    """
    if not DEEPSEEK_KEY or not caption.strip():
        return {"is_payment": False, "source": "deepseek_unavailable"}

    prompt = (
        "Apakah teks berikut adalah konfirmasi/bukti pembayaran pelanggan? "
        "Extract info jika iya. Return ONLY JSON:\n"
        '{"is_payment":true/false,"bank_name":"...or null","amount":12345,"reference":"...or null"}\n\n'
        f"Teks: {caption[:400]}"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"},
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                },
            )
            resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        result["source"] = "deepseek_text"
        result["confidence"] = 0.65 if result.get("is_payment") else 0.2
        return result
    except Exception as exc:
        return {"is_payment": False, "source": "deepseek_error", "error": str(exc)}


# ── Vision OCR (Tier-2) ───────────────────────────────────────────────────────

async def ocr_payment_image(local_path: str, caption: str = "") -> dict[str, Any]:
    """
    Tier-2 vision OCR using gpt-4o-mini on a local JPEG.
    If OpenAI key is missing/unavailable, falls back to DeepSeek text
    classification of the caption (Tier-1.5).
    Returns parsed payment dict or {is_payment: false}.
    """
    if not OPENAI_KEY:
        # Graceful degradation: try DeepSeek text fallback on caption
        if caption and DEEPSEEK_KEY:
            return await classify_payment_caption(caption)
        return {"is_payment": False, "error": "no OPENAI_API_KEY"}
    try:
        with open(local_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
    except OSError as exc:
        return {"is_payment": False, "error": str(exc)}

    prompt = (
        "Apakah gambar ini bukti transfer/pembayaran bank/QRIS/e-wallet? "
        "Jika iya return JSON: "
        '{"is_payment":true,"bank_name":"...","amount":12345,"datetime":"...","reference":"...","recipient":"..."}. '
        'Jika bukan: {"is_payment":false}. Only return JSON, no markdown.'
    )

    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {OPENAI_KEY}"},
            json={
                "model": OCR_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}},
                    ],
                }],
                "max_tokens": 200,
            },
        )
        resp.raise_for_status()

    raw_content = resp.json()["choices"][0]["message"]["content"].strip()
    # Strip markdown code fences if present
    raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
    raw_content = re.sub(r"\s*```$", "", raw_content)
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError:
        return {"is_payment": False, "raw": raw_content}


# ── SQLite helpers ─────────────────────────────────────────────────────────────

def iter_invoice_messages(since_ts: Optional[str] = None):
    """Yield rows from wa_messages that contain FAKTUR ELEKTRONIK."""
    conn = sqlite3.connect(GOWA_DB)
    conn.row_factory = sqlite3.Row
    try:
        where = "message_text LIKE '%FAKTUR ELEKTRONIK%' AND is_from_me = 1"
        params: list[Any] = []
        if since_ts:
            where += " AND timestamp > ?"
            params.append(since_ts)
        rows = conn.execute(
            f"SELECT * FROM wa_messages WHERE {where} ORDER BY timestamp ASC",
            params,
        ).fetchall()
        for row in rows:
            yield dict(row)
    finally:
        conn.close()


def iter_payment_images(since_ts: Optional[str] = None, conversation_jids: Optional[set] = None):
    """Yield customer-sent image/text messages that may contain payment proof."""
    conn = sqlite3.connect(GOWA_DB)
    conn.row_factory = sqlite3.Row
    try:
        where_parts = ["is_from_me = 0", "(message_type = 'image' OR (message_type = 'text' AND message_text LIKE '%Rp%'))"]
        params: list[Any] = []
        if since_ts:
            where_parts.append("timestamp > ?")
            params.append(since_ts)
        if conversation_jids:
            placeholders = ",".join("?" * len(conversation_jids))
            where_parts.append(f"conversation_jid IN ({placeholders})")
            params.extend(conversation_jids)
        rows = conn.execute(
            f"SELECT * FROM wa_messages WHERE {' AND '.join(where_parts)} ORDER BY timestamp ASC",
            params,
        ).fetchall()
        for row in rows:
            yield dict(row)
    finally:
        conn.close()
