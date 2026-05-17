"""
WA CRM API — SIJI Bintaro
Endpoints for WhatsApp conversation management, analytics, and LLM insights.
Database: PostgreSQL siji_bintaro schema (wa_conversations + wa_messages), synced from GOWA every 15 min.
"""

import csv
import io
import json
import os
import glob
import re
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Query, Request, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
import jwt as pyjwt

from database import get_db_connection, release_db_connection

router = APIRouter(prefix="/api/dashboard/wa", tags=["whatsapp"])

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
LLM_MODEL = "minimax-m2.5:cloud"
GOWA_MEDIA_PATH = "/opt/gowa/storages"

_jwt_secret = os.environ.get("JWT_SECRET")
if not _jwt_secret:
    raise RuntimeError("JWT_SECRET env var is required but not set")
JWT_SECRET: str = _jwt_secret
COOKIE_NAME = "siji_session"


# ─── Auth ─────────────────────────────────────────────────────────────────────

def _require_auth(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Login required")
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session")


# ─── DB ───────────────────────────────────────────────────────────────────────

def get_db():
    return get_db_connection()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_RISK_PROMPT = """\
Kamu adalah analis CRM untuk usaha laundry premium. Analisis percakapan WhatsApp berikut dari satu pelanggan.

Tugas:
1. Apakah ada indikator KOMPLAIN (keluhan, barang rusak/hilang, lamban, kecewa, marah)?
2. Apakah ada indikator CHURN (tidak puas, ingin pindah, membanding-bandingkan, mengancam berhenti)?

Pesan terakhir pelanggan:
{messages}

Jawab HANYA dengan JSON valid:
{{
  "risk_level": "none|low|medium|high",
  "complaint": true/false,
  "churn_risk": true/false,
  "indicators": ["daftar frase spesifik yang menunjukkan risiko"],
  "summary": "ringkasan singkat 1 kalimat"
}}
Jika tidak ada risiko, kembalikan risk_level "none", complaint false, churn_risk false, indicators [].
"""


def normalize_phone(phone) -> str:
    if phone is None:
        return ""
    s = str(phone).strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = re.sub(r"[^\d]", "", s)
    if s.startswith("0"):
        s = "62" + s[1:]
    return s


# ─── Conversations ────────────────────────────────────────────────────────────

@router.get("/conversations")
async def get_conversations(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    type: str = Query("all", regex="^(all|customer|unknown)$"),
    sort: str = Query("newest", regex="^(newest|risk)$"),
):
    _require_auth(request)
    conn = get_db()
    try:
        offset = (page - 1) * limit

        # Exclude @lid (WhatsApp Linked Device IDs - not real phone numbers) and
        # @broadcast (outgoing broadcast lists - not inbound CRM conversations)
        jid_filter = "AND c.jid NOT LIKE '%@lid' AND c.jid NOT LIKE '%@broadcast'"
        if type == "customer":
            where = f"WHERE c.is_group = FALSE AND c.customer_name IS NOT NULL {jid_filter}"
        elif type == "unknown":
            where = f"WHERE c.is_group = FALSE AND c.customer_name IS NULL {jid_filter}"
        else:
            where = f"WHERE c.is_group = FALSE {jid_filter}"

        total = conn.execute(
            f"SELECT COUNT(*) FROM wa_conversations c {where}"
        ).fetchone()[0]

        # risk sort: high→medium→low keyword-hint→none, then newest within each tier
        if sort == "risk":
            order_clause = """
                ORDER BY
                    CASE COALESCE(rf.risk_level, 'none')
                        WHEN 'high'   THEN 0
                        WHEN 'medium' THEN 1
                        WHEN 'low'    THEN 2
                        ELSE               3
                    END,
                    CASE WHEN rf.risk_level IS NULL AND (
                        LOWER(COALESCE(c.last_message,'')) LIKE '%komplain%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%kecewa%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%rusak%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%hilang%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%lambat%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%lama%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%mahal%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%pindah%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%batal%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%minta refund%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%tidak puas%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%nggak puas%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%ga puas%' OR
                        LOWER(COALESCE(c.last_message,'')) LIKE '%mengecewakan%'
                    ) THEN 0 ELSE 1 END,
                    sort_ts DESC
            """
        else:
            order_clause = "ORDER BY sort_ts DESC"

        rows = conn.execute(f"""
            SELECT c.jid, c.phone, c.contact_name, c.customer_name,
                   c.last_message, c.last_message_time, c.unread_count, c.total_messages,
                   t.total_orders, t.total_revenue, t.last_order,
                   COALESCE(lm.actual_last_ts, c.last_message_time) AS sort_ts,
                   rf.risk_level, rf.is_complaint, rf.is_churn, rf.indicators, rf.analyzed_at
            FROM wa_conversations c
            LEFT JOIN (
                SELECT customer_phone,
                       COUNT(*) as total_orders,
                       SUM(total_tagihan) as total_revenue,
                       MAX(date_of_transaction) as last_order
                FROM transactions
                WHERE customer_phone IS NOT NULL AND customer_phone != ''
                GROUP BY customer_phone
            ) t ON c.phone = t.customer_phone
            LEFT JOIN (
                SELECT conversation_jid, MAX(timestamp) AS actual_last_ts
                FROM wa_messages GROUP BY conversation_jid
            ) lm ON lm.conversation_jid = c.jid
            LEFT JOIN wa_risk_flags rf ON rf.jid = c.jid
            {where}
            {order_clause}
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

        conversations = []
        for r in rows:
            conversations.append({
                "jid": r["jid"],
                "phone": r["phone"],
                "contact_name": r["contact_name"],
                "customer_name": r["customer_name"],
                "display_name": r["customer_name"] or r["contact_name"] or r["phone"],
                "last_message": (r["last_message"] or "")[:80],
                "last_message_time": r["sort_ts"] or r["last_message_time"],
                "unread_count": r["unread_count"] or 0,
                "total_messages": r["total_messages"] or 0,
                "total_orders": r["total_orders"] or 0,
                "total_revenue": r["total_revenue"] or 0,
                "last_order": r["last_order"],
                "risk_level": r["risk_level"],
                "is_complaint": bool(r["is_complaint"]) if r["is_complaint"] is not None else False,
                "is_churn": bool(r["is_churn"]) if r["is_churn"] is not None else False,
                "indicators": json.loads(r["indicators"]) if r["indicators"] else [],
                "risk_analyzed_at": r["analyzed_at"],
            })

        return {
            "conversations": conversations,
            "total": total,
            "page": page,
            "pages": -(-total // limit),
        }
    finally:
        release_db_connection(conn)


# ─── Messages ─────────────────────────────────────────────────────────────────

@router.get("/messages")
async def get_messages(
    request: Request,
    phone: str = Query(..., min_length=5),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    _require_auth(request)
    conn = get_db()
    try:
        jid = phone + "@s.whatsapp.net" if "@" not in phone else phone
        offset = (page - 1) * limit

        total = conn.execute(
            "SELECT COUNT(*) FROM wa_messages WHERE conversation_jid = ?", (jid,)
        ).fetchone()[0]

        rows = conn.execute("""
            SELECT message_id, sender_jid, sender_name, message_text, message_type,
                   media_url, is_from_me, is_bot, is_forwarded, quoted_message_id, timestamp, status
            FROM wa_messages WHERE conversation_jid = ?
            ORDER BY timestamp ASC LIMIT ? OFFSET ?
        """, (jid, limit, offset)).fetchall()

        messages = [dict(r) for r in rows]

        conv = conn.execute(
            "SELECT * FROM wa_conversations WHERE jid = ?", (jid,)
        ).fetchone()

        customer = None
        if conv:
            p = conv["phone"]
            tx = conn.execute("""
                SELECT customer_name, customer_address, COUNT(*) as total_orders,
                       SUM(total_tagihan) as total_revenue, MAX(date_of_transaction) as last_order
                FROM transactions
                WHERE REPLACE(REPLACE(REPLACE(customer_phone,'+',''),'-',''),' ','') = ?
                   OR (customer_phone LIKE '0%' AND '62' || SUBSTR(REPLACE(REPLACE(REPLACE(customer_phone,'+',''),'-',''),' ',''),2) = ?)
                GROUP BY REPLACE(REPLACE(REPLACE(customer_phone,'+',''),'-',''),' ','')
            """, (p, p)).fetchone()
            customer = {
                "phone": p,
                "contact_name": conv["contact_name"],
                "customer_name": conv["customer_name"] or (tx["customer_name"] if tx else None),
                "address": tx["customer_address"] if tx else None,
                "total_orders": tx["total_orders"] if tx else 0,
                "total_revenue": tx["total_revenue"] if tx else 0,
                "last_order": tx["last_order"] if tx else None,
                "total_messages": conv["total_messages"],
            }

        return {
            "messages": messages,
            "customer": customer,
            "total": total,
            "page": page,
            "pages": -(-total // limit),
        }
    finally:
        release_db_connection(conn)


# ─── Stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(request: Request):
    _require_auth(request)
    conn = get_db()
    try:
        total_conv = conn.execute(
            "SELECT COUNT(*) FROM wa_conversations WHERE is_group = FALSE"
            " AND jid NOT LIKE '%@lid' AND jid NOT LIKE '%@broadcast'"
        ).fetchone()[0]
        total_msg = conn.execute("SELECT COUNT(*) FROM wa_messages").fetchone()[0]
        today = datetime.now().strftime("%Y-%m-%d")
        total_today = conn.execute(
            "SELECT COUNT(*) FROM wa_messages WHERE timestamp LIKE ?", (today + "%",)
        ).fetchone()[0]
        unread = conn.execute(
            "SELECT SUM(unread_count) FROM wa_conversations"
            " WHERE jid NOT LIKE '%@lid' AND jid NOT LIKE '%@broadcast'"
        ).fetchone()[0] or 0

        top = conn.execute("""
            SELECT c.phone, c.customer_name, c.contact_name, c.total_messages
            FROM wa_conversations c WHERE c.is_group = FALSE
              AND c.jid NOT LIKE '%@lid' AND c.jid NOT LIKE '%@broadcast'
            ORDER BY c.total_messages DESC LIMIT 10
        """).fetchall()
        top_contacted = [
            {
                "phone": r["phone"],
                "name": r["customer_name"] or r["contact_name"] or r["phone"],
                "message_count": r["total_messages"],
            }
            for r in top
        ]

        rows = conn.execute("""
            SELECT LEFT(timestamp, 10) as day, COUNT(*) as count,
                   SUM(CASE WHEN NOT is_from_me THEN 1 ELSE 0 END) as incoming,
                   SUM(CASE WHEN is_from_me THEN 1 ELSE 0 END) as outgoing
            FROM wa_messages
            WHERE timestamp >= (CURRENT_DATE - INTERVAL '30 days')::text
            GROUP BY LEFT(timestamp, 10) ORDER BY day
        """).fetchall()
        messages_by_day = [
            {"date": r["day"], "total": r["count"], "incoming": r["incoming"], "outgoing": r["outgoing"]}
            for r in rows
        ]

        return {
            "total_conversations": total_conv,
            "total_messages": total_msg,
            "total_today": total_today,
            "unread_count": unread,
            "top_contacted": top_contacted,
            "messages_by_day": messages_by_day,
        }
    finally:
        release_db_connection(conn)


# ─── Search ───────────────────────────────────────────────────────────────────

@router.get("/search")
async def search_messages(
    request: Request,
    q: str = Query(..., min_length=2, max_length=100),
    limit: int = Query(50, ge=1, le=100),
):
    _require_auth(request)
    conn = get_db()
    try:
        pattern = f"%{q}%"
        rows = conn.execute("""
            SELECT m.message_id, m.conversation_jid, m.sender_name, m.message_text,
                   m.message_type, m.timestamp, m.is_from_me,
                   c.phone, c.customer_name, c.contact_name
            FROM wa_messages m
            JOIN wa_conversations c ON c.jid = m.conversation_jid
            WHERE m.message_text LIKE ?
            ORDER BY m.timestamp DESC LIMIT ?
        """, (pattern, limit)).fetchall()

        return {
            "results": [
                {
                    "message_id": r["message_id"],
                    "jid": r["conversation_jid"],
                    "phone": r["phone"],
                    "name": r["customer_name"] or r["contact_name"] or r["phone"],
                    "sender": r["sender_name"],
                    "text": r["message_text"],
                    "type": r["message_type"],
                    "timestamp": r["timestamp"],
                    "is_from_me": r["is_from_me"],
                }
                for r in rows
            ],
            "count": len(rows),
            "query": q,
        }
    finally:
        release_db_connection(conn)


# ─── Export ───────────────────────────────────────────────────────────────────

@router.get("/export")
async def export_chat(
    request: Request,
    phone: str = Query(..., min_length=5),
    format: str = Query("csv", regex="^(csv|json)$"),
):
    _require_auth(request)
    conn = get_db()
    try:
        jid = phone + "@s.whatsapp.net" if "@" not in phone else phone
        rows = conn.execute("""
            SELECT timestamp, sender_jid, sender_name, message_text, message_type, media_url, is_from_me
            FROM wa_messages WHERE conversation_jid = ?
            ORDER BY timestamp ASC
        """, (jid,)).fetchall()

        if format == "json":
            data = [dict(r) for r in rows]
            filename = f"wa_chat_{phone}_{datetime.now().strftime('%Y%m%d')}.json"
            return StreamingResponse(
                io.BytesIO(json.dumps(data, ensure_ascii=False).encode("utf-8")),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Timestamp", "Sender", "Name", "Message", "Type", "Media", "FromMe"])
        for r in rows:
            writer.writerow([
                r["timestamp"], r["sender_jid"], r["sender_name"],
                r["message_text"], r["message_type"], r["media_url"] or "",
                "Yes" if r["is_from_me"] else "No",
            ])

        output.seek(0)
        filename = f"wa_chat_{phone}_{datetime.now().strftime('%Y%m%d')}.csv"
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    finally:
        release_db_connection(conn)


# ─── Analytics ────────────────────────────────────────────────────────────────

@router.get("/analytics")
async def get_analytics(request: Request):
    _require_auth(request)
    conn = get_db()
    try:
        now = datetime.now()
        d30 = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")

        # Volume per day
        vol_rows = conn.execute("""
            SELECT LEFT(timestamp, 10) as day,
                   SUM(CASE WHEN NOT is_from_me THEN 1 ELSE 0 END) as incoming,
                   SUM(CASE WHEN is_from_me THEN 1 ELSE 0 END) as outgoing
            FROM wa_messages WHERE timestamp >= %s GROUP BY LEFT(timestamp, 10) ORDER BY day
        """, (d30,)).fetchall()
        messages_by_day = [
            {"date": r["day"], "incoming": r["incoming"], "outgoing": r["outgoing"]}
            for r in vol_rows
        ]

        total_30d = conn.execute(
            "SELECT COUNT(*) FROM wa_messages WHERE timestamp >= ?", (d30,)
        ).fetchone()[0]

        # Peak hours
        peak = conn.execute("""
            SELECT EXTRACT(HOUR FROM timestamp::timestamp)::INTEGER as hour, COUNT(*) as count
            FROM wa_messages WHERE timestamp >= %s
            GROUP BY hour ORDER BY hour
        """, (d30,)).fetchall()
        peak_hours = [{"hour": r["hour"], "count": r["count"]} for r in peak]
        busiest = max(peak_hours, key=lambda x: x["count"])["hour"] if peak_hours else 0

        # Contact counts
        total_contacted = conn.execute(
            "SELECT COUNT(*) FROM wa_conversations WHERE is_group = FALSE"
            " AND jid NOT LIKE '%@lid' AND jid NOT LIKE '%@broadcast'"
        ).fetchone()[0]
        new_30d = conn.execute(
            "SELECT COUNT(*) FROM wa_conversations WHERE created_at >= %s AND is_group = FALSE"
            " AND jid NOT LIKE '%@lid' AND jid NOT LIKE '%@broadcast'", (d30,)
        ).fetchone()[0]

        most_active = conn.execute("""
            SELECT c.phone, c.customer_name, c.contact_name, COUNT(m.id) as msg_count
            FROM wa_messages m JOIN wa_conversations c ON c.jid = m.conversation_jid
            WHERE m.timestamp >= %s AND c.is_group = FALSE
              AND c.jid NOT LIKE '%@lid' AND c.jid NOT LIKE '%@broadcast'
            GROUP BY c.jid, c.phone, c.customer_name, c.contact_name ORDER BY msg_count DESC LIMIT 10
        """, (d30,)).fetchall()
        most_active_list = [
            {
                "phone": r["phone"],
                "name": r["customer_name"] or r["contact_name"] or r["phone"],
                "message_count": r["msg_count"],
            }
            for r in most_active
        ]

        # Silent customers (WA contact, no orders last 60 days)
        silent = conn.execute("""
            SELECT c.phone, c.customer_name, c.contact_name, c.last_message_time
            FROM wa_conversations c
            WHERE c.is_group = FALSE AND c.customer_name IS NOT NULL
              AND c.jid NOT LIKE '%@lid' AND c.jid NOT LIKE '%@broadcast'
              AND c.phone NOT IN (
                SELECT REPLACE(REPLACE(REPLACE(customer_phone,'+',''),'-',''),' ','')
                FROM transactions WHERE date_of_transaction >= CURRENT_DATE - INTERVAL '60 days'
                AND customer_phone IS NOT NULL
              )
            ORDER BY c.last_message_time DESC LIMIT 20
        """).fetchall()
        silent_list = []
        for r in silent:
            days = 0
            if r["last_message_time"]:
                try:
                    last = datetime.fromisoformat(r["last_message_time"].replace("Z", ""))
                    days = (now - last).days
                except Exception:
                    pass
            silent_list.append({
                "phone": r["phone"],
                "name": r["customer_name"] or r["contact_name"] or r["phone"],
                "last_contact": r["last_message_time"],
                "days_silent": days,
            })

        # Topic keyword counts
        def count_topic(keywords):
            conds = " OR ".join(f"LOWER(message_text) LIKE '%%{kw}%%'" for kw in keywords)
            return conn.execute(
                f"SELECT COUNT(*) FROM wa_messages WHERE NOT is_from_me AND timestamp >= %s AND ({conds})",
                (d30,),
            ).fetchone()[0]

        topics = {
            "inquiry":   count_topic(["tanya", "berapa", "harga", "bisa", "info", "price"]),
            "complaint": count_topic(["lama", "belum", "kapan", "complaint", "complain", "kecewa"]),
            "order":     count_topic(["order", "laundry", "cuci", "ambil", "antar", "jemput", "kirim"]),
            "feedback":  count_topic(["bagus", "terima kasih", "puas", "mantap", "makasih", "thanks"]),
        }

        # Response time via SQL window functions — no Python loop
        rt_row = conn.execute("""
            WITH pairs AS (
                SELECT
                    timestamp AS in_ts,
                    LEAD(timestamp) OVER (PARTITION BY conversation_jid ORDER BY timestamp) AS out_ts,
                    is_from_me,
                    LEAD(is_from_me) OVER (PARTITION BY conversation_jid ORDER BY timestamp) AS next_from_me
                FROM wa_messages
                WHERE timestamp >= %s
                  AND conversation_jid LIKE '%%@s.whatsapp.net'
            ),
            valid AS (
                SELECT
                    EXTRACT(EPOCH FROM (out_ts::timestamp - in_ts::timestamp)) / 60.0 AS diff_min
                FROM pairs
                WHERE NOT is_from_me AND next_from_me = TRUE
                  AND out_ts IS NOT NULL
                  AND EXTRACT(EPOCH FROM (out_ts::timestamp - in_ts::timestamp)) / 60.0 BETWEEN 0 AND 1440
            )
            SELECT
                ROUND(AVG(diff_min)::numeric, 1) as avg_minutes,
                COUNT(*) as sample_count
            FROM valid
        """, (d30,)).fetchone()

        avg_rt = rt_row["avg_minutes"] or 0
        sample_count = rt_row["sample_count"] or 0

        # RT trend last 7 days
        rt_by_day = conn.execute("""
            WITH pairs AS (
                SELECT
                    LEFT(timestamp, 10) AS day,
                    timestamp AS in_ts,
                    LEAD(timestamp) OVER (PARTITION BY conversation_jid ORDER BY timestamp) AS out_ts,
                    is_from_me,
                    LEAD(is_from_me) OVER (PARTITION BY conversation_jid ORDER BY timestamp) AS next_from_me
                FROM wa_messages
                WHERE timestamp >= (CURRENT_DATE - INTERVAL '7 days')::text
                  AND conversation_jid LIKE '%@s.whatsapp.net'
            )
            SELECT
                day,
                ROUND(AVG(EXTRACT(EPOCH FROM (out_ts::timestamp - in_ts::timestamp)) / 60.0)::numeric, 1) as avg_minutes
            FROM pairs
            WHERE NOT is_from_me AND next_from_me = TRUE
              AND out_ts IS NOT NULL
              AND EXTRACT(EPOCH FROM (out_ts::timestamp - in_ts::timestamp)) / 60.0 BETWEEN 0 AND 1440
            GROUP BY day
            ORDER BY day
        """).fetchall()
        trend_7d = [{"date": r["day"], "avg_minutes": r["avg_minutes"] or 0} for r in rt_by_day]

        return {
            "response_time": {
                "avg_minutes": avg_rt,
                "sample_count": sample_count,
                "trend_7d": trend_7d,
            },
            "volume": {
                "total_messages_30d": total_30d,
                "messages_by_day": messages_by_day,
                "peak_hours": peak_hours,
                "busiest_hour": busiest,
            },
            "customers": {
                "total_contacted": total_contacted,
                "new_contacts_30d": new_30d,
                "most_active": most_active_list,
                "silent_customers": silent_list,
            },
            "topics": topics,
        }
    finally:
        release_db_connection(conn)


# ─── LLM Insights ─────────────────────────────────────────────────────────────

@router.get("/insights")
async def get_wa_insights(request: Request):
    _require_auth(request)
    conn = get_db()
    try:
        d30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
        total = conn.execute(
            "SELECT COUNT(*) FROM wa_messages WHERE timestamp >= ?", (d30,)
        ).fetchone()[0]
        incoming = conn.execute(
            "SELECT COUNT(*) FROM wa_messages WHERE timestamp >= %s AND NOT is_from_me", (d30,)
        ).fetchone()[0]
        outgoing = total - incoming
        convs = conn.execute(
            "SELECT COUNT(*) FROM wa_conversations WHERE is_group = FALSE"
            " AND jid NOT LIKE '%@lid' AND jid NOT LIKE '%@broadcast'"
        ).fetchone()[0]

        def cnt(kws):
            c = " OR ".join(f"LOWER(message_text) LIKE '%%{k}%%'" for k in kws)
            return conn.execute(
                f"SELECT COUNT(*) FROM wa_messages WHERE NOT is_from_me AND timestamp >= %s AND ({c})", (d30,)
            ).fetchone()[0]

        topics = {
            "harga/inquiry": cnt(["harga", "berapa", "bisa", "tanya"]),
            "order/laundry": cnt(["order", "cuci", "laundry", "ambil", "antar"]),
            "keluhan":        cnt(["lama", "belum", "kapan", "kecewa"]),
            "positif":        cnt(["bagus", "terima kasih", "puas", "mantap", "makasih"]),
        }

        prompt_text = (
            f"Kamu adalah analis CRM untuk bisnis laundry SIJI Bintaro.\n"
            f"Berdasarkan data WhatsApp 30 hari terakhir:\n"
            f"- Total pesan: {total} ({incoming} masuk, {outgoing} keluar)\n"
            f"- Total kontak: {convs}\n"
            f"- Topic breakdown: {json.dumps(topics)}\n\n"
            f"Berikan insight singkat (max 200 kata, bahasa Indonesia) tentang:\n"
            f"1. Kualitas respons customer service\n"
            f"2. Pola pertanyaan pelanggan yang bisa di-improve\n"
            f"3. Rekomendasi actionable untuk minggu depan\n\n"
            f"Gunakan bullet points, ringkas dan actionable."
        )

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    OLLAMA_URL,
                    json={
                        "model": LLM_MODEL,
                        "messages": [
                            {
                                "role": "system",
                                "content": "Kamu adalah analis CRM untuk bisnis laundry SIJI Bintaro. Berikan insight singkat dalam bahasa Indonesia dengan bullet points.",
                            },
                            {"role": "user", "content": prompt_text},
                        ],
                        "stream": False,
                        "options": {"temperature": 0.5, "num_predict": 300},
                    },
                )
                result = resp.json()
                insight_text = result.get("message", {}).get("content", "")
        except Exception as e:
            insight_text = f"LLM tidak tersedia: {e}"

        return {
            "insight": insight_text,
            "data_summary": {
                "total_messages": total,
                "incoming": incoming,
                "outgoing": outgoing,
                "conversations": convs,
                "topics": topics,
            },
        }
    finally:
        release_db_connection(conn)


# ─── Unified Customer Profiles ────────────────────────────────────────────────

@router.get("/customers/profiles")
async def get_customer_profiles(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    segment: str = Query("all", regex="^(all|VIP|Reguler|Baru)$"),
    search: str = Query(""),
    has_wa: bool = Query(False),
    sort_by: str = Query(
        "total_transaksi",
        regex="^(total_transaksi|total_belanja|last_transaksi|nama_transaksi)$",
    ),
):
    _require_auth(request)
    conn = get_db()
    try:
        offset = (page - 1) * limit
        conditions = []
        params = []

        if segment != "all":
            conditions.append("segment = ?")
            params.append(segment)
        if search:
            conditions.append("(nama_transaksi LIKE ? OR nama_wa LIKE ? OR phone LIKE ?)")
            params += [f"%{search}%", f"%{search}%", f"%{search}%"]
        if has_wa:
            conditions.append("ada_wa = TRUE")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM v_customer_profiles {where}", params
        ).fetchone()[0]

        rows = conn.execute(f"""
            SELECT phone, nama_wa, nama_transaksi, alamat,
                   total_transaksi, total_belanja, avg_belanja,
                   first_transaksi, last_transaksi,
                   total_pesan_wa, last_pesan_wa, segment, ada_wa
            FROM v_customer_profiles {where}
            ORDER BY {sort_by} DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        customers = [
            {
                "phone": r["phone"],
                "nama": r["nama_wa"] or r["nama_transaksi"],
                "nama_transaksi": r["nama_transaksi"],
                "nama_wa": r["nama_wa"],
                "alamat": r["alamat"],
                "segment": r["segment"],
                "total_transaksi": r["total_transaksi"],
                "total_belanja": r["total_belanja"],
                "avg_belanja": r["avg_belanja"],
                "first_transaksi": r["first_transaksi"],
                "last_transaksi": r["last_transaksi"],
                "total_pesan_wa": r["total_pesan_wa"],
                "last_pesan_wa": (r["last_pesan_wa"] or "")[:80],
                "ada_wa": bool(r["ada_wa"]),
                "wa_link": f"https://wa.me/{r['phone']}" if r["phone"] else None,
            }
            for r in rows
        ]

        segment_summary = {}
        for seg in ["VIP", "Reguler", "Baru"]:
            segment_summary[seg] = conn.execute(
                "SELECT COUNT(*) FROM v_customer_profiles WHERE segment = ?", (seg,)
            ).fetchone()[0]

        return {
            "customers": customers,
            "total": total,
            "page": page,
            "pages": -(-total // limit),
            "segment_summary": segment_summary,
            "wa_linked": conn.execute(
                "SELECT COUNT(*) FROM v_customer_profiles WHERE ada_wa = TRUE"
            ).fetchone()[0],
        }
    finally:
        release_db_connection(conn)


@router.get("/customers/profile/{phone}")
async def get_customer_profile(phone: str, request: Request):
    _require_auth(request)
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT phone, nama_wa, nama_transaksi, alamat,
                   total_transaksi, total_belanja, avg_belanja,
                   first_transaksi, last_transaksi,
                   total_pesan_wa, last_pesan_wa, segment, ada_wa
            FROM v_customer_profiles WHERE phone = ?
        """, (phone,)).fetchone()

        if not row:
            return {"found": False, "phone": phone}

        services = conn.execute("""
            SELECT td.nama_layanan, COUNT(*) as freq,
                   ROUND(AVG(td.total_item), 0) as avg_harga
            FROM transaction_details td
            JOIN transactions t ON t.no_nota = td.no_nota
            WHERE t.customer_phone = ?
            GROUP BY td.nama_layanan
            ORDER BY freq DESC LIMIT 10
        """, (phone,)).fetchall()

        recent_tx = conn.execute("""
            SELECT no_nota, date_of_transaction, total_tagihan,
                   nama_layanan, progress_status, pembayaran
            FROM transactions WHERE customer_phone = ?
            ORDER BY date_of_transaction DESC LIMIT 5
        """, (phone,)).fetchall()

        return {
            "found": True,
            "phone": row["phone"],
            "nama": row["nama_wa"] or row["nama_transaksi"],
            "nama_transaksi": row["nama_transaksi"],
            "nama_wa": row["nama_wa"],
            "alamat": row["alamat"],
            "segment": row["segment"],
            "total_transaksi": row["total_transaksi"],
            "total_belanja": row["total_belanja"],
            "avg_belanja": row["avg_belanja"],
            "first_transaksi": row["first_transaksi"],
            "last_transaksi": row["last_transaksi"],
            "total_pesan_wa": row["total_pesan_wa"],
            "ada_wa": bool(row["ada_wa"]),
            "wa_link": f"https://wa.me/{phone}",
            "favorite_services": [
                {"nama": s["nama_layanan"], "freq": s["freq"], "avg_harga": s["avg_harga"]}
                for s in services
            ],
            "recent_transactions": [
                {
                    "no_nota": t["no_nota"],
                    "tanggal": t["date_of_transaction"],
                    "total": t["total_tagihan"],
                    "layanan": t["nama_layanan"],
                    "status": t["progress_status"],
                    "pembayaran": t["pembayaran"],
                }
                for t in recent_tx
            ],
        }
    finally:
        release_db_connection(conn)


# ─── Risk / Complaint-Churn Analysis ─────────────────────────────────────────

@router.get("/risk-flags")
async def get_risk_flags(request: Request):
    """Return all cached risk flags keyed by JID."""
    _require_auth(request)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT jid, phone, risk_level, is_complaint, is_churn, indicators, summary, analyzed_at FROM wa_risk_flags"
        ).fetchall()
        return {
            r["jid"]: {
                "risk_level": r["risk_level"],
                "is_complaint": bool(r["is_complaint"]),
                "is_churn": bool(r["is_churn"]),
                "indicators": json.loads(r["indicators"]) if r["indicators"] else [],
                "summary": r["summary"],
                "analyzed_at": r["analyzed_at"],
            }
            for r in rows
        }
    finally:
        release_db_connection(conn)


@router.post("/risk-scan")
async def risk_scan(request: Request, limit: int = Query(30, ge=1, le=100)):
    """
    Analyze the most-recent `limit` conversations with DeepSeek for
    complaint / churn indicators. Results are cached in wa_risk_flags.
    """
    _require_auth(request)
    api_key = DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise HTTPException(502, "DEEPSEEK_API_KEY not configured")

    conn = get_db()
    try:
        # Fetch conversations with most-recent actual message
        recent = conn.execute("""
            SELECT c.jid, c.phone
            FROM wa_conversations c
            LEFT JOIN (
                SELECT conversation_jid, MAX(timestamp) AS last_ts
                FROM wa_messages GROUP BY conversation_jid
            ) lm ON lm.conversation_jid = c.jid
            WHERE c.is_group = FALSE
              AND c.jid NOT LIKE '%@lid' AND c.jid NOT LIKE '%@broadcast'
            ORDER BY COALESCE(lm.last_ts, c.last_message_time) DESC
            LIMIT ?
        """, (limit,)).fetchall()

        results = []
        for row in recent:
            jid, phone = row["jid"], row["phone"]

            # Fetch last 15 customer-sent messages
            msgs = conn.execute("""
                SELECT message_text, timestamp FROM wa_messages
                WHERE conversation_jid = ? AND NOT is_from_me AND message_text IS NOT NULL AND TRIM(message_text) != ''
                ORDER BY timestamp DESC LIMIT 15
            """, (jid,)).fetchall()

            if not msgs:
                continue

            msg_text = "\n".join(
                f"[{m['timestamp'][:16] if m['timestamp'] else '?'}] {m['message_text']}"
                for m in reversed(msgs)
            )

            prompt = DEEPSEEK_RISK_PROMPT.format(messages=msg_text)
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        "https://api.deepseek.com/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={
                            "model": "deepseek-chat",
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.2,
                            "max_tokens": 300,
                        },
                    )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                # Strip code fences if present
                if raw.startswith("```"):
                    raw = re.sub(r"^```[a-z]*\n?", "", raw)
                    raw = re.sub(r"```$", "", raw).strip()
                analysis = json.loads(raw)
            except Exception as e:
                analysis = {
                    "risk_level": "none", "complaint": False,
                    "churn_risk": False, "indicators": [], "summary": f"parse error: {e}"
                }

            risk_level   = analysis.get("risk_level", "none")
            is_complaint = bool(analysis.get("complaint"))
            is_churn     = bool(analysis.get("churn_risk"))
            indicators   = json.dumps(analysis.get("indicators", []), ensure_ascii=False)
            summary      = analysis.get("summary", "")

            conn.execute("""
                INSERT INTO wa_risk_flags (jid, phone, risk_level, is_complaint, is_churn, indicators, summary, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NOW())
                ON CONFLICT(jid) DO UPDATE SET
                    risk_level=EXCLUDED.risk_level,
                    is_complaint=EXCLUDED.is_complaint,
                    is_churn=EXCLUDED.is_churn,
                    indicators=EXCLUDED.indicators,
                    summary=EXCLUDED.summary,
                    analyzed_at=EXCLUDED.analyzed_at
            """, (jid, phone, risk_level, is_complaint, is_churn, indicators, summary))
            conn.commit()

            results.append({
                "jid": jid, "phone": phone,
                "risk_level": risk_level,
                "is_complaint": bool(is_complaint),
                "is_churn": bool(is_churn),
                "summary": summary,
            })

        return {"scanned": len(results), "flagged": sum(1 for r in results if r["risk_level"] != "none"), "results": results}
    finally:
        release_db_connection(conn)


# ─── Media ────────────────────────────────────────────────────────────────────

@router.get("/media/list")
async def list_recent_media(request: Request, limit: int = 50):
    _require_auth(request)
    files = glob.glob(f"{GOWA_MEDIA_PATH}/*.jpe") + glob.glob(f"{GOWA_MEDIA_PATH}/*.jpg")
    files.sort(key=os.path.getmtime, reverse=True)
    return [
        {"name": os.path.basename(f), "size": os.path.getsize(f)}
        for f in files[:limit]
    ]


@router.get("/media/by-timestamp/{timestamp}")
async def serve_media_by_timestamp(timestamp: str, request: Request):
    _require_auth(request)
    try:
        if "T" in timestamp:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            unix_ts = int(dt.timestamp())
        else:
            unix_ts = int(timestamp)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid timestamp")

    matches = []
    for f in glob.glob(f"{GOWA_MEDIA_PATH}/*.jpe"):
        fname = os.path.basename(f)
        try:
            file_ts = int(fname.split("-")[0])
            if abs(file_ts - unix_ts) <= 5:
                matches.append(f)
        except Exception:
            continue

    if not matches:
        raise HTTPException(status_code=404, detail="No media found")

    return FileResponse(matches[0], media_type="image/jpeg")


@router.get("/payment-images")
async def list_payment_images(
    request: Request,
    days: int = Query(default=14, ge=1, le=60),
    limit: int = Query(default=50, ge=1, le=200),
):
    _require_auth(request)
    conn = get_db()
    try:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        rows = conn.execute(
            """SELECT m.timestamp, m.message_text, m.media_url, m.conversation_jid,
                      c.phone, c.customer_name, c.contact_name, m.sender_name
               FROM wa_messages m
               JOIN wa_conversations c ON c.jid = m.conversation_jid
               WHERE m.message_type = 'image'
                 AND NOT m.is_from_me
                 AND m.timestamp >= ?
               ORDER BY m.timestamp DESC
               LIMIT ?""",
            (since, limit),
        ).fetchall()

        results = []
        for r in rows:
            ts = r["timestamp"]
            local_file = None
            unix_ts = None
            try:
                dt = datetime.fromisoformat(ts.replace("Z", ""))
                unix_ts = int(dt.timestamp())
                for f in glob.glob(f"{GOWA_MEDIA_PATH}/*.jpe") + glob.glob(f"{GOWA_MEDIA_PATH}/*.jpg"):
                    fname = os.path.basename(f)
                    try:
                        if abs(int(fname.split("-")[0]) - unix_ts) <= 5:
                            local_file = os.path.basename(f)
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            context_msgs = []
            if unix_ts:
                try:
                    dt = datetime.utcfromtimestamp(unix_ts)
                    before = (dt - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
                    after  = (dt + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
                    ctx_rows = conn.execute("""
                        SELECT message_text, is_from_me, timestamp, message_type
                        FROM wa_messages
                        WHERE conversation_jid = ? AND timestamp BETWEEN ? AND ?
                        ORDER BY timestamp
                    """, (r["conversation_jid"], before, after)).fetchall()
                    for cm in ctx_rows:
                        if cm["message_text"] and cm["message_text"] != r["message_text"]:
                            context_msgs.append({
                                "text": cm["message_text"],
                                "is_me": bool(cm["is_from_me"]),
                                "type": cm["message_type"],
                                "time": cm["timestamp"],
                            })
                except Exception:
                    pass

            phone = r["phone"] or r["conversation_jid"].split("@")[0]
            name = r["customer_name"] or r["contact_name"] or r["sender_name"] or phone

            results.append({
                "timestamp": ts,
                "unix_timestamp": unix_ts,
                "phone": phone,
                "customer_name": name,
                "message_text": r["message_text"],
                "media_url": r["media_url"],
                "local_file": local_file,
                "image_url": f"/api/dashboard/wa/media/by-timestamp/{ts}" if local_file else None,
                "context": context_msgs[:6],
            })

        return {"images": results, "count": len(results), "days": days}
    finally:
        release_db_connection(conn)


@router.get("/media/{filename}")
async def serve_media(filename: str, request: Request):
    _require_auth(request)
    allowed_ext = (".jpe", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".pdf")
    if not filename.lower().endswith(allowed_ext):
        raise HTTPException(status_code=400, detail="Invalid file type")

    filepath = os.path.join(GOWA_MEDIA_PATH, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")

    ext = os.path.splitext(filename)[1].lower()
    content_types = {
        ".jpe": "image/jpeg", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
        ".mp4": "video/mp4", ".pdf": "application/pdf",
    }
    return FileResponse(filepath, media_type=content_types.get(ext, "application/octet-stream"))
