"""
WA CRM API — SIJI Bintaro
Endpoints for WhatsApp conversation management, analytics, and LLM insights.
Database: /opt/siji-dashboard/siji_database.db (wa_conversations + wa_messages tables)
"""

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from typing import Optional
import sqlite3
import io
import csv
import re
import json
import urllib.request
from datetime import datetime, timedelta

router = APIRouter(prefix="/api/dashboard/wa", tags=["whatsapp"])

DB_PATH = "/opt/siji-dashboard/siji_database.db"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
LLM_MODEL = "minimax-m2.5:cloud"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    type: str = Query("all", regex="^(all|customer|unknown)$"),
):
    conn = get_db()
    try:
        offset = (page - 1) * limit

        if type == "customer":
            where = "WHERE c.is_group = 0 AND c.customer_name IS NOT NULL"
        elif type == "unknown":
            where = "WHERE c.is_group = 0 AND c.customer_name IS NULL"
        else:
            where = "WHERE c.is_group = 0"

        total = conn.execute(
            f"SELECT COUNT(*) FROM wa_conversations c {where}"
        ).fetchone()[0]

        rows = conn.execute(f"""
            SELECT c.jid, c.phone, c.contact_name, c.customer_name,
                   c.last_message, c.last_message_time, c.unread_count, c.total_messages,
                   t.total_orders, t.total_revenue, t.last_order
            FROM wa_conversations c
            LEFT JOIN (
                SELECT customer_phone,
                       COUNT(*) as total_orders,
                       SUM(total_tagihan) as total_revenue,
                       MAX(date_of_transaction) as last_order
                FROM transactions
                WHERE customer_phone IS NOT NULL AND customer_phone != ''
                GROUP BY customer_phone
            ) t ON c.phone = REPLACE(REPLACE(REPLACE(t.customer_phone, '+', ''), '-', ''), ' ', '')
                OR c.phone = (CASE WHEN t.customer_phone LIKE '0%'
                              THEN '62' || SUBSTR(REPLACE(REPLACE(REPLACE(t.customer_phone, '+', ''), '-', ''), ' ', ''), 2)
                              ELSE REPLACE(REPLACE(REPLACE(t.customer_phone, '+', ''), '-', ''), ' ', '') END)
            {where}
            ORDER BY c.last_message_time DESC
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
                "last_message_time": r["last_message_time"],
                "unread_count": r["unread_count"] or 0,
                "total_messages": r["total_messages"] or 0,
                "total_orders": r["total_orders"] or 0,
                "total_revenue": r["total_revenue"] or 0,
                "last_order": r["last_order"],
            })

        return {"conversations": conversations, "total": total, "page": page, "pages": -(-total // limit)}
    finally:
        conn.close()


# ─── Messages ─────────────────────────────────────────────────────────────────

@router.get("/messages")
async def get_messages(
    phone: str = Query(..., min_length=5),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        jid = phone + "@s.whatsapp.net" if "@" not in phone else phone
        offset = (page - 1) * limit

        total = conn.execute(
            "SELECT COUNT(*) FROM wa_messages WHERE conversation_jid = ?", (jid,)
        ).fetchone()[0]

        rows = conn.execute("""
            SELECT message_id, sender_jid, sender_name, message_text, message_type,
                   media_url, is_from_me, is_forwarded, quoted_message_id, timestamp, status
            FROM wa_messages WHERE conversation_jid = ?
            ORDER BY timestamp ASC LIMIT ? OFFSET ?
        """, (jid, limit, offset)).fetchall()

        messages = [dict(r) for r in rows]

        # Customer info
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

        return {"messages": messages, "customer": customer, "total": total, "page": page, "pages": -(-total // limit)}
    finally:
        conn.close()


# ─── Stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats():
    conn = get_db()
    try:
        total_conv = conn.execute("SELECT COUNT(*) FROM wa_conversations WHERE is_group = 0").fetchone()[0]
        total_msg = conn.execute("SELECT COUNT(*) FROM wa_messages").fetchone()[0]
        today = datetime.now().strftime("%Y-%m-%d")
        total_today = conn.execute(
            "SELECT COUNT(*) FROM wa_messages WHERE timestamp LIKE ?", (today + "%",)
        ).fetchone()[0]
        unread = conn.execute(
            "SELECT SUM(unread_count) FROM wa_conversations"
        ).fetchone()[0] or 0

        # Top contacted
        top = conn.execute("""
            SELECT c.phone, c.customer_name, c.contact_name, c.total_messages
            FROM wa_conversations c WHERE c.is_group = 0
            ORDER BY c.total_messages DESC LIMIT 10
        """).fetchall()
        top_contacted = [{"phone": r["phone"], "name": r["customer_name"] or r["contact_name"] or r["phone"],
                          "message_count": r["total_messages"]} for r in top]

        # Messages by day (last 30 days)
        rows = conn.execute("""
            SELECT DATE(timestamp) as day, COUNT(*) as count,
                   SUM(CASE WHEN is_from_me = 0 THEN 1 ELSE 0 END) as incoming,
                   SUM(CASE WHEN is_from_me = 1 THEN 1 ELSE 0 END) as outgoing
            FROM wa_messages
            WHERE timestamp >= DATE('now', '-30 days')
            GROUP BY DATE(timestamp) ORDER BY day
        """).fetchall()
        messages_by_day = [{"date": r["day"], "total": r["count"],
                            "incoming": r["incoming"], "outgoing": r["outgoing"]} for r in rows]

        return {
            "total_conversations": total_conv,
            "total_messages": total_msg,
            "total_today": total_today,
            "unread_count": unread,
            "top_contacted": top_contacted,
            "messages_by_day": messages_by_day,
        }
    finally:
        conn.close()


# ─── Search ───────────────────────────────────────────────────────────────────

@router.get("/search")
async def search_messages(
    q: str = Query(..., min_length=2, max_length=100),
    limit: int = Query(50, ge=1, le=100),
):
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

        results = [{
            "message_id": r["message_id"],
            "jid": r["conversation_jid"],
            "phone": r["phone"],
            "name": r["customer_name"] or r["contact_name"] or r["phone"],
            "sender": r["sender_name"],
            "text": r["message_text"],
            "type": r["message_type"],
            "timestamp": r["timestamp"],
            "is_from_me": r["is_from_me"],
        } for r in rows]

        return {"results": results, "count": len(results), "query": q}
    finally:
        conn.close()


# ─── Export ───────────────────────────────────────────────────────────────────

@router.get("/export")
async def export_chat(
    phone: str = Query(..., min_length=5),
    format: str = Query("csv", regex="^csv$"),
):
    conn = get_db()
    try:
        jid = phone + "@s.whatsapp.net" if "@" not in phone else phone
        rows = conn.execute("""
            SELECT timestamp, sender_jid, sender_name, message_text, message_type, media_url, is_from_me
            FROM wa_messages WHERE conversation_jid = ?
            ORDER BY timestamp ASC
        """, (jid,)).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Timestamp", "Sender", "Name", "Message", "Type", "Media", "FromMe"])
        for r in rows:
            writer.writerow([r["timestamp"], r["sender_jid"], r["sender_name"],
                             r["message_text"], r["message_type"], r["media_url"] or "",
                             "Yes" if r["is_from_me"] else "No"])

        output.seek(0)
        filename = f"wa_chat_{phone}_{datetime.now().strftime('%Y%m%d')}.csv"
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    finally:
        conn.close()


# ─── Analytics ────────────────────────────────────────────────────────────────

@router.get("/analytics")
async def get_analytics():
    conn = get_db()
    try:
        now = datetime.now()
        d30 = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")

        # Volume
        vol_rows = conn.execute("""
            SELECT DATE(timestamp) as day,
                   SUM(CASE WHEN is_from_me = 0 THEN 1 ELSE 0 END) as incoming,
                   SUM(CASE WHEN is_from_me = 1 THEN 1 ELSE 0 END) as outgoing
            FROM wa_messages WHERE timestamp >= ? GROUP BY DATE(timestamp) ORDER BY day
        """, (d30,)).fetchall()
        messages_by_day = [{"date": r["day"], "incoming": r["incoming"], "outgoing": r["outgoing"]} for r in vol_rows]

        total_30d = conn.execute(
            "SELECT COUNT(*) FROM wa_messages WHERE timestamp >= ?", (d30,)
        ).fetchone()[0]

        # Peak hours
        peak = conn.execute("""
            SELECT CAST(SUBSTR(timestamp, 12, 2) AS INTEGER) as hour, COUNT(*) as count
            FROM wa_messages WHERE timestamp >= ?
            GROUP BY hour ORDER BY hour
        """, (d30,)).fetchall()
        peak_hours = [{"hour": r["hour"], "count": r["count"]} for r in peak]
        busiest = max(peak_hours, key=lambda x: x["count"])["hour"] if peak_hours else 0

        # Customers
        total_contacted = conn.execute(
            "SELECT COUNT(*) FROM wa_conversations WHERE is_group = 0"
        ).fetchone()[0]

        new_30d = conn.execute(
            "SELECT COUNT(*) FROM wa_conversations WHERE created_at >= ? AND is_group = 0", (d30,)
        ).fetchone()[0]

        most_active = conn.execute("""
            SELECT c.phone, c.customer_name, c.contact_name, COUNT(m.id) as msg_count
            FROM wa_messages m JOIN wa_conversations c ON c.jid = m.conversation_jid
            WHERE m.timestamp >= ? AND c.is_group = 0
            GROUP BY c.jid ORDER BY msg_count DESC LIMIT 10
        """, (d30,)).fetchall()
        most_active_list = [{"phone": r["phone"],
                             "name": r["customer_name"] or r["contact_name"] or r["phone"],
                             "message_count": r["msg_count"]} for r in most_active]

        # Silent customers: have WA contact but 0 orders in last 60 days
        silent = conn.execute("""
            SELECT c.phone, c.customer_name, c.contact_name, c.last_message_time
            FROM wa_conversations c
            WHERE c.is_group = 0 AND c.customer_name IS NOT NULL
              AND c.phone NOT IN (
                SELECT REPLACE(REPLACE(REPLACE(customer_phone,'+',''),'-',''),' ','')
                FROM transactions WHERE date_of_transaction >= DATE('now', '-60 days')
                AND customer_phone IS NOT NULL
              )
            ORDER BY c.last_message_time DESC LIMIT 20
        """).fetchall()
        silent_list = []
        for r in silent:
            days = 0
            if r["last_message_time"]:
                try:
                    last = datetime.fromisoformat(r["last_message_time"].replace("Z", "+00:00"))
                    days = (now - last.replace(tzinfo=None)).days
                except Exception:
                    pass
            silent_list.append({"phone": r["phone"],
                                "name": r["customer_name"] or r["contact_name"] or r["phone"],
                                "last_contact": r["last_message_time"], "days_silent": days})

        # Topics (keyword-based)
        def count_topic(keywords):
            conds = " OR ".join(f"LOWER(message_text) LIKE '%{kw}%'" for kw in keywords)
            return conn.execute(
                f"SELECT COUNT(*) FROM wa_messages WHERE is_from_me = 0 AND timestamp >= ? AND ({conds})", (d30,)
            ).fetchone()[0]

        topics = {
            "inquiry": count_topic(["tanya", "berapa", "harga", "bisa", "info", "price"]),
            "complaint": count_topic(["lama", "belum", "kapan", "complaint", "complain", "kecewa"]),
            "order": count_topic(["order", "laundry", "cuci", "ambil", "antar", "jemput", "kirim"]),
            "feedback": count_topic(["bagus", "terima kasih", "puas", "mantap", "makasih", "thanks"]),
        }

        # Response time (avg time between incoming and next outgoing in same conversation)
        rt_rows = conn.execute("""
            SELECT conversation_jid, timestamp, is_from_me
            FROM wa_messages WHERE timestamp >= ? AND conversation_jid LIKE '%@s.whatsapp.net'
            ORDER BY conversation_jid, timestamp
        """, (d30,)).fetchall()

        response_times = []
        last_incoming = {}
        for r in rt_rows:
            jid = r["conversation_jid"]
            ts = r["timestamp"]
            if not r["is_from_me"]:
                last_incoming[jid] = ts
            elif jid in last_incoming:
                try:
                    t_in = datetime.fromisoformat(last_incoming[jid].replace("Z", "+00:00")).replace(tzinfo=None)
                    t_out = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                    diff = (t_out - t_in).total_seconds() / 60
                    if 0 < diff < 1440:  # within 24h
                        response_times.append(diff)
                except Exception:
                    pass
                del last_incoming[jid]

        avg_rt = round(sum(response_times) / len(response_times), 1) if response_times else 0
        sorted_rt = sorted(response_times)
        median_rt = round(sorted_rt[len(sorted_rt) // 2], 1) if sorted_rt else 0

        # RT by day (last 7 days)
        rt_by_day = []
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            day_rts = []
            day_rows = conn.execute("""
                SELECT conversation_jid, timestamp, is_from_me
                FROM wa_messages WHERE DATE(timestamp) = ? AND conversation_jid LIKE '%@s.whatsapp.net'
                ORDER BY conversation_jid, timestamp
            """, (day,)).fetchall()
            li = {}
            for r in day_rows:
                j, ts = r["conversation_jid"], r["timestamp"]
                if not r["is_from_me"]:
                    li[j] = ts
                elif j in li:
                    try:
                        t1 = datetime.fromisoformat(li[j].replace("Z", "+00:00")).replace(tzinfo=None)
                        t2 = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                        d = (t2 - t1).total_seconds() / 60
                        if 0 < d < 1440:
                            day_rts.append(d)
                    except Exception:
                        pass
                    del li[j]
            avg = round(sum(day_rts) / len(day_rts), 1) if day_rts else 0
            rt_by_day.append({"date": day, "avg_minutes": avg})

        return {
            "response_time": {"avg_minutes": avg_rt, "median_minutes": median_rt, "trend_7d": rt_by_day},
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
        conn.close()


# ─── LLM Insights ────────────────────────────────────────────────────────────

@router.get("/insights")
async def get_wa_insights():
    conn = get_db()
    try:
        d30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
        total = conn.execute("SELECT COUNT(*) FROM wa_messages WHERE timestamp >= ?", (d30,)).fetchone()[0]
        incoming = conn.execute("SELECT COUNT(*) FROM wa_messages WHERE timestamp >= ? AND is_from_me = 0", (d30,)).fetchone()[0]
        outgoing = total - incoming
        convs = conn.execute("SELECT COUNT(*) FROM wa_conversations WHERE is_group = 0").fetchone()[0]

        # Simple topic counts
        def cnt(kws):
            c = " OR ".join(f"LOWER(message_text) LIKE '%{k}%'" for k in kws)
            return conn.execute(f"SELECT COUNT(*) FROM wa_messages WHERE is_from_me=0 AND timestamp>=? AND ({c})", (d30,)).fetchone()[0]

        topics = {"harga/inquiry": cnt(["harga", "berapa", "bisa", "tanya"]),
                  "order/laundry": cnt(["order", "cuci", "laundry", "ambil", "antar"]),
                  "keluhan": cnt(["lama", "belum", "kapan", "kecewa"]),
                  "positif": cnt(["bagus", "terima kasih", "puas", "mantap", "makasih"])}

        prompt_text = f"""Kamu adalah analis CRM untuk bisnis laundry SIJI Bintaro.
Berdasarkan data WhatsApp 30 hari terakhir:
- Total pesan: {total} ({incoming} masuk, {outgoing} keluar)
- Total kontak: {convs}
- Topic breakdown: {json.dumps(topics)}

Berikan insight singkat (max 200 kata, bahasa Indonesia) tentang:
1. Kualitas respons customer service
2. Pola pertanyaan pelanggan yang bisa di-improve
3. Rekomendasi actionable untuk minggu depan

Gunakan bullet points, ringkas dan actionable."""

        try:
            payload = json.dumps({"model": LLM_MODEL, "prompt": prompt_text, "stream": False,
                                  "options": {"temperature": 0.5, "num_predict": 500}}).encode()
            req = urllib.request.Request(OLLAMA_URL, data=payload,
                                        headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                insight_text = result.get("response", "")
        except Exception as e:
            insight_text = f"LLM tidak tersedia: {e}"

        return {
            "insight": insight_text,
            "data_summary": {"total_messages": total, "incoming": incoming, "outgoing": outgoing,
                             "conversations": convs, "topics": topics},
        }
    finally:
        conn.close()


# ─── Unified Customer Profiles (v_customer_profiles) ─────────────────────────

@router.get("/customers/profiles")
async def get_customer_profiles(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    segment: str = Query("all", regex="^(all|VIP|Reguler|Baru)$"),
    search: str = Query(""),
    has_wa: bool = Query(False),
    sort_by: str = Query("total_transaksi", regex="^(total_transaksi|total_belanja|last_transaksi|nama_transaksi)$"),
):
    """
    Unified customer list dari transaksi + WA.
    Source: v_customer_profiles (transactions LEFT JOIN wa_conversations).
    """
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
            conditions.append("ada_wa = 1")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        total = conn.execute(f"SELECT COUNT(*) FROM v_customer_profiles {where}", params).fetchone()[0]

        rows = conn.execute(f"""
            SELECT phone, nama_wa, nama_transaksi, alamat,
                   total_transaksi, total_belanja, avg_belanja,
                   first_transaksi, last_transaksi,
                   total_pesan_wa, last_pesan_wa, segment, ada_wa
            FROM v_customer_profiles {where}
            ORDER BY {sort_by} DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        customers = []
        for r in rows:
            customers.append({
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
            })

        segment_summary = {}
        for seg in ["VIP", "Reguler", "Baru"]:
            n = conn.execute("SELECT COUNT(*) FROM v_customer_profiles WHERE segment = ?", (seg,)).fetchone()[0]
            segment_summary[seg] = n

        return {
            "customers": customers,
            "total": total,
            "page": page,
            "pages": -(-total // limit),
            "segment_summary": segment_summary,
            "wa_linked": conn.execute("SELECT COUNT(*) FROM v_customer_profiles WHERE ada_wa = 1").fetchone()[0],
        }
    finally:
        conn.close()


@router.get("/customers/profile/{phone}")
async def get_customer_profile(phone: str):
    """
    Detail profil satu customer: transaksi + WA + histori layanan.
    """
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

        # Layanan yang pernah digunakan
        services = conn.execute("""
            SELECT td.nama_layanan, COUNT(*) as freq,
                   ROUND(AVG(td.total_item), 0) as avg_harga
            FROM transaction_details td
            JOIN transactions t ON t.no_nota = td.no_nota
            WHERE t.customer_phone = ?
            GROUP BY td.nama_layanan
            ORDER BY freq DESC LIMIT 10
        """, (phone,)).fetchall()

        # 5 transaksi terakhir
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
            "favorite_services": [{"nama": s["nama_layanan"], "freq": s["freq"], "avg_harga": s["avg_harga"]} for s in services],
            "recent_transactions": [{
                "no_nota": t["no_nota"],
                "tanggal": t["date_of_transaction"],
                "total": t["total_tagihan"],
                "layanan": t["nama_layanan"],
                "status": t["progress_status"],
                "pembayaran": t["pembayaran"],
            } for t in recent_tx],
        }
    finally:
        conn.close()
