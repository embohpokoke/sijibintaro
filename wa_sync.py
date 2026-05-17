"""
wa_sync.py — GOWA → PostgreSQL sync pipeline for SIJI Bintaro
Syncs WhatsApp conversations and messages from GOWA API to PostgreSQL siji_bintaro schema.
"""

import json
import os
import re
import sys
from datetime import datetime

import psycopg2
import psycopg2.extras
import requests

# Config
GOWA_BASE = "http://127.0.0.1:3002"
GOWA_AUTH = ("siji", "SijiBintaro2026!")
PAGE_SIZE = 100

DATABASE_URL = os.getenv(
    "SIJI_DB_URL",
    "postgresql://livin:L1v1n!B1nt4r0_2026@127.0.0.1:5432/livininbintaro",
)
DB_SCHEMA = "siji_bintaro"


def get_pg_conn():
    conn = psycopg2.connect(DATABASE_URL, options=f"-c search_path={DB_SCHEMA},public")
    conn.autocommit = False
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
    if not s.startswith("62") and len(s) > 8:
        s = "62" + s
    return s


def jid_to_phone(jid: str) -> str:
    return jid.split("@")[0] if "@" in jid else jid


def is_group_jid(jid: str) -> bool:
    return "@g.us" in jid


def is_standard_jid(jid: str) -> bool:
    """Return True for standard individual or group JIDs that belong in the CRM.
    Excludes @lid (Linked Device IDs — opaque numeric identifiers, not real phone numbers)
    and @broadcast (outgoing broadcast lists).
    """
    return "@lid" not in jid and "@broadcast" not in jid


def gowa_get(endpoint: str, params: dict = None) -> dict:
    url = f"{GOWA_BASE}/{endpoint.lstrip('/')}"
    resp = requests.get(url, auth=GOWA_AUTH, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def sync_all_conversations(conn) -> int:
    offset = 0
    total_synced = 0
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    with conn.cursor() as cur:
        while True:
            data = gowa_get("/chats", {"offset": offset, "limit": PAGE_SIZE})
            results = data.get("results", {})
            chats = results.get("data", [])
            pagination = results.get("pagination", {})

            if not chats:
                break

            for chat in chats:
                jid = chat.get("jid", "")
                if not jid or not is_standard_jid(jid):
                    continue

                phone = jid_to_phone(jid)
                is_group = is_group_jid(jid)
                contact_name = chat.get("name", "")
                last_msg_time = chat.get("last_message_time", "")

                cur.execute("""
                    INSERT INTO wa_conversations (jid, phone, contact_name, is_group, last_message_time, synced_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (jid) DO UPDATE SET
                        contact_name = COALESCE(NULLIF(EXCLUDED.contact_name, ''), wa_conversations.contact_name),
                        last_message_time = EXCLUDED.last_message_time,
                        synced_at = EXCLUDED.synced_at
                """, (jid, phone, contact_name, is_group, last_msg_time, now))
                total_synced += 1

            offset += PAGE_SIZE
            total = pagination.get("total", 0)
            if offset >= total:
                break

    conn.commit()
    return total_synced


def sync_messages(conn, jid: str) -> int:
    offset = 0
    total_inserted = 0
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    with conn.cursor() as cur:
        while True:
            data = gowa_get(f"/chat/{jid}/messages", {"offset": offset, "limit": PAGE_SIZE})
            results = data.get("results", {})
            messages = results.get("data", [])
            pagination = results.get("pagination", {})

            if not messages:
                break

            for msg in messages:
                msg_id = msg.get("id", "")
                if not msg_id:
                    continue

                content = msg.get("content", "")
                media_type = msg.get("media_type", "")
                message_type = media_type if media_type else "text"
                media_url = msg.get("url", "")
                filename = msg.get("filename", "")
                if media_type and not content:
                    content = f"[{media_type}: {filename}]" if filename else f"[{media_type}]"

                sender_jid = msg.get("sender_jid", "")
                is_from_me = bool(msg.get("is_from_me", False))
                timestamp = msg.get("timestamp", "")

                cur.execute("""
                    INSERT INTO wa_messages
                        (conversation_jid, message_id, sender_jid, message_text, message_type,
                         media_url, is_from_me, timestamp, synced_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (message_id) DO NOTHING
                """, (jid, msg_id, sender_jid, content, message_type,
                      media_url or None, is_from_me, timestamp, now))
                if cur.rowcount:
                    total_inserted += 1

            offset += PAGE_SIZE
            total = pagination.get("total", 0)
            if offset >= total:
                break

        # Update conversation stats
        cur.execute("SELECT COUNT(*) FROM wa_messages WHERE conversation_jid = %s", (jid,))
        msg_count = cur.fetchone()[0]

        cur.execute("""
            SELECT message_text, timestamp FROM wa_messages
            WHERE conversation_jid = %s ORDER BY timestamp DESC LIMIT 1
        """, (jid,))
        last = cur.fetchone()
        if last:
            cur.execute("""
                UPDATE wa_conversations
                SET total_messages = %s, last_message = %s, last_message_time = %s, synced_at = %s
                WHERE jid = %s
            """, (msg_count, last[0], last[1], now, jid))

    conn.commit()
    return total_inserted


def sync_contact_names(conn) -> int:
    try:
        data = gowa_get("/user/my/contacts")
    except Exception as e:
        print(f"  [WARN] Failed to fetch contacts: {e}")
        return 0

    contacts = data.get("results", {}).get("data", [])
    updated = 0
    with conn.cursor() as cur:
        for c in contacts:
            jid = c.get("jid", "")
            name = c.get("name", "").strip()
            if not jid or not name or "@s.whatsapp.net" not in jid:
                continue
            if re.match(r"^[\+\d\s\-]+$", name):
                continue
            cur.execute("""
                UPDATE wa_conversations SET contact_name = %s
                WHERE jid = %s AND (contact_name IS NULL OR contact_name = '' OR contact_name = phone)
            """, (name, jid))
            if cur.rowcount:
                updated += 1

    conn.commit()
    return updated


def link_customers(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT customer_phone, customer_name
            FROM transactions
            WHERE customer_phone IS NOT NULL AND customer_phone != ''
        """)
        tx_map = {}
        for row in cur.fetchall():
            phone = normalize_phone(row[0])
            if phone and phone not in tx_map:
                tx_map[phone] = row[1]

        cur.execute("SELECT jid, phone FROM wa_conversations WHERE is_group = FALSE")
        linked = 0
        for row in cur.fetchall():
            jid, phone = row
            norm_phone = normalize_phone(phone)
            if norm_phone in tx_map:
                cur.execute(
                    "UPDATE wa_conversations SET customer_name = %s WHERE jid = %s",
                    (tx_map[norm_phone], jid))
                linked += 1

    conn.commit()
    return linked


def full_sync():
    print(f"[{datetime.now().isoformat()}] Starting GOWA → PostgreSQL sync")

    try:
        resp = requests.get(f"{GOWA_BASE}/devices", auth=GOWA_AUTH, timeout=5)
        if resp.status_code == 401:
            print("[ERROR] GOWA auth failed")
            return
    except requests.ConnectionError:
        print("[ERROR] GOWA not reachable at", GOWA_BASE)
        return

    conn = get_pg_conn()
    try:
        print("[1/4] Syncing conversations...")
        conv_count = sync_all_conversations(conn)
        print(f"  → {conv_count} conversations synced")

        print("[2/4] Syncing messages...")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT jid FROM wa_conversations WHERE is_group = FALSE ORDER BY last_message_time DESC")
            jids = [row[0] for row in cur.fetchall()]

        total_msgs = 0
        for i, jid in enumerate(jids):
            try:
                count = sync_messages(conn, jid)
                total_msgs += count
                if (i + 1) % 50 == 0:
                    print(f"  → {i+1}/{len(jids)} chats processed ({total_msgs} new messages)")
            except Exception as e:
                print(f"  [WARN] Failed to sync {jid}: {e}")
                continue
        print(f"  → {total_msgs} new messages from {len(jids)} chats")

        print("[3/4] Syncing saved contact names...")
        contact_count = sync_contact_names(conn)
        print(f"  → {contact_count} contact names updated")

        print("[4/4] Linking to transaction customers...")
        linked = link_customers(conn)
        print(f"  → {linked} conversations linked to customers")

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM wa_conversations")
            conv_total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM wa_messages")
            msg_total = cur.fetchone()[0]
        print(f"\nSync complete: {conv_total} conversations, {msg_total} messages total")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Sync failed: {e}")
        raise
    finally:
        conn.close()

    print(f"[{datetime.now().isoformat()}] Done")


if __name__ == "__main__":
    full_sync()
