#!/usr/bin/env python3
"""
wa_history_import.py — Import GOWA WhatsApp history JSON files into PostgreSQL.

GOWA writes history sync events from WhatsApp to JSON files in /opt/gowa/storages/.
This script parses all history-*.json files and upserts every message + conversation
into the siji_bintaro PostgreSQL schema, ensuring full historical coverage.

Usage:
    python3 wa_history_import.py              # import all history files
    python3 wa_history_import.py --dry-run    # count without writing
"""

import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

HISTORY_PATH = "/opt/gowa/storages/history-*.json"
DATABASE_URL = os.getenv(
    "SIJI_DB_URL",
    "postgresql://livin:L1v1n!B1nt4r0_2026@127.0.0.1:5432/livininbintaro",
)
DB_SCHEMA = "siji_bintaro"

DRY_RUN = "--dry-run" in sys.argv


def get_conn():
    conn = psycopg2.connect(DATABASE_URL, options=f"-c search_path={DB_SCHEMA},public")
    conn.autocommit = False
    return conn


def extract_text(msg_obj: dict) -> tuple[str, str]:
    """Extract (text_content, message_type) from a WA message object."""
    if not isinstance(msg_obj, dict):
        return "", "unknown"
    inner = msg_obj.get("message", {}) or {}
    if not isinstance(inner, dict):
        return "", "unknown"

    # Plain text
    if inner.get("conversation"):
        return inner["conversation"], "text"

    # Extended text (links, etc.)
    ext = inner.get("extendedTextMessage")
    if isinstance(ext, dict) and ext.get("text"):
        return ext["text"], "text"

    # Image with optional caption
    img = inner.get("imageMessage")
    if isinstance(img, dict):
        caption = img.get("caption", "")
        return f"[image] {caption}".strip(), "image"

    # Video
    vid = inner.get("videoMessage")
    if isinstance(vid, dict):
        caption = vid.get("caption", "")
        return f"[video] {caption}".strip(), "video"

    # Audio / PTT
    if inner.get("audioMessage"):
        ptt = inner["audioMessage"].get("PTT", False)
        return "[voice note]" if ptt else "[audio]", "audio"

    # Document
    doc = inner.get("documentMessage")
    if isinstance(doc, dict):
        fname = doc.get("fileName", "")
        return f"[document: {fname}]".strip(), "document"

    # Sticker
    if inner.get("stickerMessage"):
        return "[sticker]", "sticker"

    # Location
    loc = inner.get("locationMessage")
    if isinstance(loc, dict):
        return f"[location: {loc.get('degreesLatitude')},{loc.get('degreesLongitude')}]", "location"

    # Contact
    if inner.get("contactMessage"):
        name = inner["contactMessage"].get("displayName", "")
        return f"[contact: {name}]", "contact"

    # Buttons / interactive
    if inner.get("buttonsResponseMessage"):
        return inner["buttonsResponseMessage"].get("selectedDisplayText", "[button response]"), "text"

    if inner.get("listResponseMessage"):
        return inner["listResponseMessage"].get("title", "[list response]"), "text"

    # Template
    tmpl = inner.get("templateButtonReplyMessage")
    if isinstance(tmpl, dict):
        return tmpl.get("selectedDisplayText", "[template reply]"), "text"

    return "", "unknown"


def ts_to_iso(ts) -> str:
    """Convert Unix timestamp (int or str) to ISO 8601 string."""
    if not ts:
        return ""
    try:
        t = int(ts)
        return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return str(ts)


def normalize_phone(jid: str) -> str:
    phone = jid.split("@")[0]
    phone = re.sub(r"[^\d]", "", phone)
    if phone.startswith("0"):
        phone = "62" + phone[1:]
    return phone


def is_group(jid: str) -> bool:
    return "@g.us" in jid


def is_importable_jid(jid: str) -> bool:
    """Return True only for standard individual WhatsApp JIDs (@s.whatsapp.net).
    Exclude @lid (Linked Device IDs — not real phone numbers), @broadcast, and @g.us groups.
    """
    return "@s.whatsapp.net" in jid


def import_file(conn, filepath: str) -> tuple[int, int]:
    """Import one history JSON file. Returns (convs_counted, msgs_counted).
    conn=None means dry-run (count only, no writes)."""
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    conversations = data.get("conversations", [])
    convs_done = 0
    msgs_done = 0
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for conv in conversations:
        jid = conv.get("ID") or conv.get("id") or ""
        if not jid:
            continue
        # Skip non-standard JIDs: @lid (Linked Device IDs with fake phone numbers),
        # @broadcast (outgoing broadcast lists), and group chats (@g.us)
        if not is_importable_jid(jid):
            continue

        messages = conv.get("messages", []) or []
        conv_ts = ts_to_iso(conv.get("conversationTimestamp"))
        phone = normalize_phone(jid)
        group = is_group(jid)

        if conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO wa_conversations
                        (jid, phone, is_group, last_message_time, synced_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (jid) DO UPDATE SET
                        last_message_time = CASE
                            WHEN EXCLUDED.last_message_time > wa_conversations.last_message_time
                            THEN EXCLUDED.last_message_time
                            ELSE wa_conversations.last_message_time
                        END,
                        synced_at = EXCLUDED.synced_at
                """, (jid, phone, group, conv_ts or None, now_str))
        convs_done += 1

        for msg_wrapper in messages:
            msg_obj = msg_wrapper.get("message", {}) or {}
            key = msg_obj.get("key", {}) or {}
            msg_id = key.get("ID") or key.get("id") or ""
            if not msg_id:
                continue

            remote_jid = key.get("remoteJID") or jid
            from_me = bool(key.get("fromMe", False))
            sender_jid = "" if from_me else remote_jid
            timestamp = ts_to_iso(msg_obj.get("messageTimestamp"))
            text, msg_type = extract_text(msg_obj)

            if not text and msg_type == "unknown":
                continue

            if conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO wa_messages
                            (conversation_jid, message_id, sender_jid, message_text,
                             message_type, is_from_me, timestamp, synced_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (message_id) DO UPDATE SET
                            message_text = CASE
                                WHEN EXCLUDED.message_text != '' THEN EXCLUDED.message_text
                                ELSE wa_messages.message_text
                            END,
                            message_type = EXCLUDED.message_type
                    """, (jid, msg_id, sender_jid or None, text, msg_type,
                          from_me, timestamp, now_str))
            msgs_done += 1

    if conn:
        conn.commit()

    return convs_done, msgs_done


def update_conversation_stats(conn):
    """Recompute total_messages and last_message for all conversations."""
    print("  Updating conversation stats…")
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE wa_conversations c
            SET
                total_messages = sub.cnt,
                last_message   = sub.last_text,
                last_message_time = sub.last_ts
            FROM (
                SELECT
                    conversation_jid,
                    COUNT(*)                     AS cnt,
                    MAX(timestamp)               AS last_ts,
                    (ARRAY_AGG(message_text ORDER BY timestamp DESC))[1] AS last_text
                FROM wa_messages
                GROUP BY conversation_jid
            ) sub
            WHERE c.jid = sub.conversation_jid
        """)
        updated = cur.rowcount
    conn.commit()
    print(f"  → {updated} conversation stats refreshed")


def main():
    files = sorted(glob.glob(HISTORY_PATH))
    if not files:
        print("No history files found at", HISTORY_PATH)
        return

    print(f"Found {len(files)} history file(s)")
    if DRY_RUN:
        print("DRY RUN — no writes")

    conn = get_conn() if not DRY_RUN else None

    total_convs = 0
    total_msgs = 0

    for filepath in files:
        fname = os.path.basename(filepath)
        try:
            c, m = import_file(conn if not DRY_RUN else None, filepath)
            total_convs += c
            total_msgs += m
            print(f"  {fname}: {c} convs, {m} messages")
        except Exception as e:
            print(f"  {fname}: ERROR — {e}")
            if conn:
                conn.rollback()

    print(f"\nTotal imported: {total_convs} conversation entries, {total_msgs} messages")

    if conn and not DRY_RUN:
        update_conversation_stats(conn)

        # Final counts
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM wa_conversations")
            cv = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM wa_messages")
            mv = cur.fetchone()[0]
            cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM wa_messages")
            mn, mx = cur.fetchone()
        print(f"\nPostgreSQL totals: {cv} conversations, {mv} messages")
        print(f"Coverage: {mn} → {mx}")
        conn.close()


if __name__ == "__main__":
    main()
