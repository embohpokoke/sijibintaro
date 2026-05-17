"""
SIJI Bintaro - Chatbot Analytics API
Endpoints untuk evaluasi kualitas dan usage chatbot WhatsApp
"""

from fastapi import APIRouter, Query
from typing import Optional
from datetime import datetime, timedelta
import asyncpg
import asyncio
import os

router = APIRouter(prefix="/api/chatbot", tags=["chatbot-analytics"])

DATABASE_URL = "postgresql://livin:L1v1n!B1nt4r0_2026@127.0.0.1:5432/livininbintaro"


async def get_pg():
    return await asyncpg.connect(DATABASE_URL)


# ─── Overview / KPI Cards ────────────────────────────────────────────────────

@router.get("/overview")
async def chatbot_overview(days: int = 30):
    """Statistik utama chatbot: containment rate, escalation, response time, dll"""
    conn = await get_pg()
    try:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Total conversations in period (that have at least one inbound message)
        total_convos = await conn.fetchval("""
            SELECT COUNT(DISTINCT c.jid)
            FROM siji_bintaro.wa_conversations c
            JOIN siji_bintaro.wa_messages m ON m.conversation_jid = c.jid
            WHERE m.is_from_me = FALSE AND m.timestamp >= $1 AND c.is_group = FALSE
        """, since)

        # Bot-only conversations (containment)
        bot_only = await conn.fetchval("""
            SELECT COUNT(DISTINCT jid)
            FROM siji_bintaro.wa_conversations
            WHERE handled_by = 'bot_only'
            AND jid IN (
                SELECT DISTINCT conversation_jid FROM siji_bintaro.wa_messages
                WHERE timestamp >= $1
            )
        """, since)

        # Hybrid (bot + human)
        hybrid = await conn.fetchval("""
            SELECT COUNT(DISTINCT jid)
            FROM siji_bintaro.wa_conversations
            WHERE handled_by = 'hybrid'
            AND jid IN (
                SELECT DISTINCT conversation_jid FROM siji_bintaro.wa_messages
                WHERE timestamp >= $1
            )
        """, since)

        # Escalated conversations
        escalated = await conn.fetchval("""
            SELECT COUNT(DISTINCT jid)
            FROM siji_bintaro.wa_conversations
            WHERE escalated = TRUE
            AND jid IN (
                SELECT DISTINCT conversation_jid FROM siji_bintaro.wa_messages
                WHERE timestamp >= $1
            )
        """, since)

        # Total bot messages
        bot_msgs = await conn.fetchval("""
            SELECT COUNT(*) FROM siji_bintaro.wa_messages
            WHERE is_bot = TRUE AND timestamp >= $1
        """, since)

        # Total human-sent messages (is_from_me but NOT bot)
        human_msgs = await conn.fetchval("""
            SELECT COUNT(*) FROM siji_bintaro.wa_messages
            WHERE is_from_me = TRUE AND (is_bot = FALSE OR is_bot IS NULL) AND timestamp >= $1
        """, since)

        # Total inbound messages
        inbound_msgs = await conn.fetchval("""
            SELECT COUNT(*) FROM siji_bintaro.wa_messages
            WHERE is_from_me = FALSE AND timestamp >= $1
        """, since)

        # Avg bot response time (ms)
        avg_response_ms = await conn.fetchval("""
            SELECT AVG(bot_response_ms)
            FROM siji_bintaro.wa_messages
            WHERE is_bot = TRUE AND bot_response_ms IS NOT NULL AND timestamp >= $1
        """, since)

        # LLM rate: rag_llm layer vs total bot messages
        llm_msgs = await conn.fetchval("""
            SELECT COUNT(*) FROM siji_bintaro.wa_messages
            WHERE is_bot = TRUE AND reply_layer LIKE 'rag_llm%' AND timestamp >= $1
        """, since)

        total_msgs = await conn.fetchval("""
            SELECT COUNT(*) FROM siji_bintaro.wa_messages WHERE timestamp >= $1
        """, since)

        # Unique customers who messaged
        unique_customers = await conn.fetchval("""
            SELECT COUNT(DISTINCT conversation_jid)
            FROM siji_bintaro.wa_messages
            WHERE is_from_me = FALSE AND timestamp >= $1
        """, since)

        containment_rate = round((bot_only / total_convos * 100) if total_convos else 0, 1)
        escalation_rate = round((escalated / total_convos * 100) if total_convos else 0, 1)
        llm_rate = round((llm_msgs / bot_msgs * 100) if bot_msgs else 0, 1)
        bot_ratio = round((bot_msgs / (bot_msgs + human_msgs) * 100) if (bot_msgs + human_msgs) else 0, 1)

        return {
            "period_days": days,
            "since": since,
            "conversations": {
                "total": total_convos or 0,
                "bot_only": bot_only or 0,
                "hybrid": hybrid or 0,
                "escalated": escalated or 0,
                "unique_customers": unique_customers or 0,
            },
            "messages": {
                "total": total_msgs or 0,
                "inbound": inbound_msgs or 0,
                "bot_replies": bot_msgs or 0,
                "human_replies": human_msgs or 0,
            },
            "kpis": {
                "containment_rate": containment_rate,
                "escalation_rate": escalation_rate,
                "llm_rate": llm_rate,
                "bot_ratio": bot_ratio,
                "avg_response_ms": round(avg_response_ms or 0),
                "avg_response_sec": round((avg_response_ms or 0) / 1000, 1),
            }
        }
    finally:
        await conn.close()


# ─── Daily Trend ────────────────────────────────────────────────────────────

@router.get("/daily")
async def chatbot_daily(days: int = 30):
    """Trend harian: bot vs human messages"""
    conn = await get_pg()
    try:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = await conn.fetch("""
            SELECT
                DATE(timestamp::timestamp) as day,
                COUNT(*) FILTER (WHERE is_from_me = FALSE) as inbound,
                COUNT(*) FILTER (WHERE is_bot = TRUE) as bot_replies,
                COUNT(*) FILTER (WHERE is_from_me = TRUE AND (is_bot = FALSE OR is_bot IS NULL)) as human_replies,
                COUNT(DISTINCT conversation_jid) FILTER (WHERE is_from_me = FALSE) as active_convos
            FROM siji_bintaro.wa_messages
            WHERE timestamp >= $1
            GROUP BY DATE(timestamp::timestamp)
            ORDER BY day ASC
        """, since)

        return {
            "days": days,
            "data": [
                {
                    "date": str(r["day"]),
                    "inbound": r["inbound"],
                    "bot_replies": r["bot_replies"],
                    "human_replies": r["human_replies"],
                    "active_convos": r["active_convos"],
                }
                for r in rows
            ]
        }
    finally:
        await conn.close()


# ─── Layer Distribution ──────────────────────────────────────────────────────

@router.get("/layers")
async def chatbot_layers(days: int = 30):
    """Distribusi layer autoreply: keyword, catalog, rag_llm, escalated, dll"""
    conn = await get_pg()
    try:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = await conn.fetch("""
            SELECT
                CASE
                    WHEN reply_layer LIKE 'rag_llm%' THEN 'rag_llm'
                    WHEN reply_layer LIKE 'keyword%' THEN 'keyword'
                    WHEN reply_layer LIKE 'escalated%' THEN 'escalated'
                    WHEN reply_layer LIKE 'holiday%' THEN 'holiday'
                    WHEN reply_layer LIKE 'default%' THEN 'default'
                    WHEN reply_layer IS NULL THEN 'untracked'
                    ELSE reply_layer
                END as layer_group,
                COUNT(*) as count
            FROM siji_bintaro.wa_messages
            WHERE is_bot = TRUE AND timestamp >= $1
            GROUP BY layer_group
            ORDER BY count DESC
        """, since)

        layer_labels = {
            "rag_llm": "RAG + LLM",
            "keyword": "Keyword Match",
            "catalog": "Katalog Harga",
            "escalated": "Eskalasi Komplain",
            "job": "Lamaran Kerja",
            "order_status": "Status Order",
            "ask_item": "Tanya Item",
            "holiday": "Holiday Mode",
            "default": "Fallback",
            "untracked": "Tidak Terdata",
        }

        total = sum(r["count"] for r in rows)
        return {
            "days": days,
            "total_bot_messages": total,
            "layers": [
                {
                    "layer": r["layer_group"],
                    "label": layer_labels.get(r["layer_group"], r["layer_group"]),
                    "count": r["count"],
                    "pct": round(r["count"] / total * 100, 1) if total else 0
                }
                for r in rows
            ]
        }
    finally:
        await conn.close()


# ─── Hourly Distribution ─────────────────────────────────────────────────────

@router.get("/hourly")
async def chatbot_hourly(days: int = 30):
    """Distribusi pesan per jam (WIB = UTC+7)"""
    conn = await get_pg()
    try:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = await conn.fetch("""
            SELECT
                EXTRACT(HOUR FROM (timestamp::timestamp + INTERVAL '7 hours')) as hour_wib,
                COUNT(*) FILTER (WHERE is_from_me = FALSE) as inbound,
                COUNT(*) FILTER (WHERE is_bot = TRUE) as bot_replies
            FROM siji_bintaro.wa_messages
            WHERE timestamp >= $1
            GROUP BY hour_wib
            ORDER BY hour_wib ASC
        """, since)

        hours = {int(r["hour_wib"]): {"inbound": r["inbound"], "bot_replies": r["bot_replies"]} for r in rows}
        return {
            "days": days,
            "data": [
                {
                    "hour": h,
                    "label": f"{h:02d}:00",
                    "inbound": hours.get(h, {}).get("inbound", 0),
                    "bot_replies": hours.get(h, {}).get("bot_replies", 0),
                }
                for h in range(24)
            ]
        }
    finally:
        await conn.close()


# ─── Recent Conversations ────────────────────────────────────────────────────

@router.get("/conversations")
async def chatbot_conversations(limit: int = 50, days: int = 30):
    """Daftar percakapan dengan klasifikasi bot/human"""
    conn = await get_pg()
    try:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = await conn.fetch("""
            SELECT
                c.jid,
                c.phone,
                COALESCE(c.customer_name, c.contact_name, c.phone) as name,
                c.handled_by,
                c.bot_msg_count,
                c.human_msg_count,
                c.escalated,
                c.last_bot_layer,
                c.last_message,
                c.last_message_time,
                COUNT(m.id) FILTER (WHERE m.is_from_me = FALSE) as inbound_count,
                COUNT(m.id) FILTER (WHERE m.is_bot = TRUE) as bot_count,
                COUNT(m.id) FILTER (WHERE m.is_from_me = TRUE AND (m.is_bot = FALSE OR m.is_bot IS NULL)) as human_count
            FROM siji_bintaro.wa_conversations c
            JOIN siji_bintaro.wa_messages m ON m.conversation_jid = c.jid
            WHERE m.timestamp >= $1 AND c.is_group = FALSE
            GROUP BY c.jid, c.phone, c.customer_name, c.contact_name,
                     c.handled_by, c.bot_msg_count, c.human_msg_count,
                     c.escalated, c.last_bot_layer, c.last_message, c.last_message_time
            HAVING COUNT(m.id) FILTER (WHERE m.is_from_me = FALSE) > 0
            ORDER BY c.last_message_time DESC NULLS LAST
            LIMIT $2
        """, since, limit)

        return {
            "limit": limit,
            "days": days,
            "conversations": [
                {
                    "jid": r["jid"],
                    "phone": r["phone"],
                    "name": r["name"],
                    "handled_by": r["handled_by"],
                    "bot_msg_count": r["bot_msg_count"] or 0,
                    "human_msg_count": r["human_msg_count"] or 0,
                    "escalated": r["escalated"] or False,
                    "last_bot_layer": r["last_bot_layer"],
                    "last_message": r["last_message"],
                    "last_message_time": r["last_message_time"],
                    "inbound_count": r["inbound_count"],
                    "bot_count": r["bot_count"],
                    "human_count": r["human_count"],
                }
                for r in rows
            ]
        }
    finally:
        await conn.close()


# ─── Quality Signals ─────────────────────────────────────────────────────────

@router.get("/quality")
async def chatbot_quality(days: int = 30):
    """Signal kualitas: repeat questions, fallback rate, escalation patterns"""
    conn = await get_pg()
    try:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Fallback rate (default layer = bot tidak bisa jawab)
        fallback = await conn.fetchval("""
            SELECT COUNT(*) FROM siji_bintaro.wa_messages
            WHERE is_bot = TRUE AND reply_layer LIKE 'default%' AND timestamp >= $1
        """, since)

        total_bot = await conn.fetchval("""
            SELECT COUNT(*) FROM siji_bintaro.wa_messages
            WHERE is_bot = TRUE AND timestamp >= $1
        """, since)

        # Conversations that escalated after bot tried
        escalated_after_bot = await conn.fetchval("""
            SELECT COUNT(DISTINCT c.jid)
            FROM siji_bintaro.wa_conversations c
            WHERE c.escalated = TRUE
            AND c.bot_msg_count > 0
            AND c.jid IN (
                SELECT DISTINCT conversation_jid FROM siji_bintaro.wa_messages
                WHERE timestamp >= $1
            )
        """, since)

        # Avg messages per conversation
        avg_msgs = await conn.fetchval("""
            SELECT AVG(msg_count) FROM (
                SELECT COUNT(*) as msg_count
                FROM siji_bintaro.wa_messages
                WHERE timestamp >= $1
                GROUP BY conversation_jid
            ) sub
        """, since)

        # Layer performance breakdown
        layer_perf = await conn.fetch("""
            SELECT reply_layer, COUNT(*) as count,
                   AVG(bot_response_ms) as avg_ms
            FROM siji_bintaro.wa_messages
            WHERE is_bot = TRUE AND reply_layer IS NOT NULL AND timestamp >= $1
            GROUP BY reply_layer
            ORDER BY count DESC
            LIMIT 15
        """, since)

        fallback_rate = round((fallback / total_bot * 100) if total_bot else 0, 1)

        return {
            "days": days,
            "fallback_rate": fallback_rate,
            "fallback_count": fallback or 0,
            "total_bot_messages": total_bot or 0,
            "escalated_after_bot": escalated_after_bot or 0,
            "avg_messages_per_convo": round(avg_msgs or 0, 1),
            "layer_performance": [
                {
                    "layer": r["reply_layer"],
                    "count": r["count"],
                    "avg_response_ms": round(r["avg_ms"] or 0),
                }
                for r in layer_perf
            ]
        }
    finally:
        await conn.close()
