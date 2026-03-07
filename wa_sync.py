"""
wa_sync.py — GOWA → SQLite sync pipeline for SIJI Bintaro
Syncs WhatsApp conversations and messages from GOWA API to siji_database.db.
"""

import sqlite3
import requests
import re
import sys
from datetime import datetime

# Config
GOWA_BASE = "http://127.0.0.1:3002"
GOWA_AUTH = ("siji", "SijiBintaro2026!")
TX_DB = "/opt/siji-dashboard/siji_database.db"
PAGE_SIZE = 100


def normalize_phone(phone) -> str:
    """Normalize phone number: 628xxx format."""
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
    """Extract normalized phone from JID like 628xxx@s.whatsapp.net."""
    return jid.split("@")[0] if "@" in jid else jid


def is_group_jid(jid: str) -> bool:
    return "@g.us" in jid


def gowa_get(endpoint: str, params: dict = None) -> dict:
    """GET from GOWA API with auth."""
    url = f"{GOWA_BASE}/{endpoint.lstrip('/')}"
    resp = requests.get(url, auth=GOWA_AUTH, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def sync_all_conversations(conn: sqlite3.Connection) -> int:
    """Sync all conversations from GOWA /chats endpoint."""
    offset = 0
    total_synced = 0
    now = datetime.now(tz=None).strftime("%Y-%m-%dT%H:%M:%SZ")

    while True:
        data = gowa_get("/chats", {"offset": offset, "limit": PAGE_SIZE})
        results = data.get("results", {})
        chats = results.get("data", [])
        pagination = results.get("pagination", {})

        if not chats:
            break

        for chat in chats:
            jid = chat.get("jid", "")
            if not jid or jid == "status@broadcast":
                continue

            phone = jid_to_phone(jid)
            is_group = is_group_jid(jid)
            contact_name = chat.get("name", "")
            last_msg_time = chat.get("last_message_time", "")

            conn.execute("""
                INSERT INTO wa_conversations (jid, phone, contact_name, is_group, last_message_time, synced_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(jid) DO UPDATE SET
                    contact_name = COALESCE(NULLIF(excluded.contact_name, ''), wa_conversations.contact_name),
                    last_message_time = excluded.last_message_time,
                    synced_at = excluded.synced_at
            """, (jid, phone, contact_name, is_group, last_msg_time, now))
            total_synced += 1

        offset += PAGE_SIZE
        total = pagination.get("total", 0)
        if offset >= total:
            break

    conn.commit()
    return total_synced


def sync_messages(conn: sqlite3.Connection, jid: str) -> int:
    """Sync all messages for a specific conversation from GOWA."""
    offset = 0
    total_inserted = 0
    now = datetime.now(tz=None).strftime("%Y-%m-%dT%H:%M:%SZ")

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
            is_from_me = msg.get("is_from_me", False)
            timestamp = msg.get("timestamp", "")

            try:
                conn.execute("""
                    INSERT OR IGNORE INTO wa_messages
                    (conversation_jid, message_id, sender_jid, message_text, message_type,
                     media_url, is_from_me, timestamp, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (jid, msg_id, sender_jid, content, message_type,
                      media_url or None, is_from_me, timestamp, now))
                if conn.total_changes:
                    total_inserted += 1
            except sqlite3.IntegrityError:
                pass

        offset += PAGE_SIZE
        total = pagination.get("total", 0)
        if offset >= total:
            break

    # Update conversation stats
    cursor = conn.execute(
        "SELECT COUNT(*) FROM wa_messages WHERE conversation_jid = ?", (jid,))
    msg_count = cursor.fetchone()[0]

    cursor = conn.execute("""
        SELECT message_text, timestamp FROM wa_messages
        WHERE conversation_jid = ? ORDER BY timestamp DESC LIMIT 1
    """, (jid,))
    last = cursor.fetchone()
    if last:
        conn.execute("""
            UPDATE wa_conversations
            SET total_messages = ?, last_message = ?, last_message_time = ?, synced_at = ?
            WHERE jid = ?
        """, (msg_count, last[0], last[1], now, jid))

    conn.commit()
    return total_inserted


def sync_contact_names(conn: sqlite3.Connection) -> int:
    """Sync saved contact names from GOWA /user/my/contacts endpoint."""
    import re as _re
    try:
        data = gowa_get("/user/my/contacts")
    except Exception as e:
        print(f"  [WARN] Failed to fetch contacts: {e}")
        return 0

    contacts = data.get("results", {}).get("data", [])
    updated = 0
    for c in contacts:
        jid = c.get("jid", "")
        name = c.get("name", "").strip()
        if not jid or not name or "@s.whatsapp.net" not in jid:
            continue
        # Skip if name is just a phone number
        if _re.match(r"^[\+\d\s\-]+$", name):
            continue
        # Update contact_name only if current value is empty or just phone
        conn.execute("""
            UPDATE wa_conversations SET contact_name = ?
            WHERE jid = ? AND (contact_name IS NULL OR contact_name = '' OR contact_name = phone)
        """, (name, jid))
        if conn.total_changes:
            updated += 1

    conn.commit()
    return updated


def link_customers(conn: sqlite3.Connection) -> int:
    """Link wa_conversations to transaction customers by phone number."""
    cursor = conn.execute("""
        SELECT DISTINCT customer_phone, customer_name
        FROM transactions
        WHERE customer_phone IS NOT NULL AND customer_phone != ''
    """)
    tx_map = {}
    for row in cursor.fetchall():
        phone = normalize_phone(row[0])
        if phone and phone not in tx_map:
            tx_map[phone] = row[1]

    linked = 0
    conv_cursor = conn.execute("SELECT jid, phone FROM wa_conversations WHERE is_group = 0")
    for row in conv_cursor.fetchall():
        jid, phone = row
        norm_phone = normalize_phone(phone)
        if norm_phone in tx_map:
            conn.execute(
                "UPDATE wa_conversations SET customer_name = ? WHERE jid = ?",
                (tx_map[norm_phone], jid))
            linked += 1

    conn.commit()
    return linked


def full_sync():
    """Run full sync pipeline."""
    print(f"[{datetime.now().isoformat()}] Starting GOWA → SQLite sync")

    # Check GOWA is reachable
    try:
        resp = requests.get(f"{GOWA_BASE}/devices", auth=GOWA_AUTH, timeout=5)
        if resp.status_code == 401:
            print("[ERROR] GOWA auth failed")
            return
    except requests.ConnectionError:
        print("[ERROR] GOWA not reachable at", GOWA_BASE)
        return

    conn = sqlite3.connect(TX_DB)
    try:
        # Step 1: Sync conversations
        print("[1/3] Syncing conversations...")
        conv_count = sync_all_conversations(conn)
        print(f"  → {conv_count} conversations synced")

        # Step 2: Sync messages for each conversation (skip groups & status)
        print("[2/3] Syncing messages...")
        cursor = conn.execute(
            "SELECT jid FROM wa_conversations WHERE is_group = 0 ORDER BY last_message_time DESC")
        jids = [row[0] for row in cursor.fetchall()]
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

        # Step 3: Sync saved contact names from phone
        print("[3/4] Syncing saved contact names...")
        contact_count = sync_contact_names(conn)
        print(f"  → {contact_count} contact names updated from phone contacts")

        # Step 4: Link customers from transaction database
        print("[4/4] Linking to transaction customers...")
        linked = link_customers(conn)
        print(f"  → {linked} conversations linked to customers")

        # Summary
        conv_total = conn.execute("SELECT COUNT(*) FROM wa_conversations").fetchone()[0]
        msg_total = conn.execute("SELECT COUNT(*) FROM wa_messages").fetchone()[0]
        print(f"\nSync complete: {conv_total} conversations, {msg_total} messages total")

    except Exception as e:
        print(f"[ERROR] Sync failed: {e}")
        raise
    finally:
        conn.close()

    print(f"[{datetime.now().isoformat()}] Done")


if __name__ == "__main__":
    full_sync()
