#!/usr/bin/env python3
"""
One-shot backfill: copy WhatsApp rows from legacy SQLite (siji_database.db)
into PostgreSQL (siji_bintaro) that are missing by message_id.

- Upserts wa_conversations for any JID referenced by backfilled messages.
- Inserts wa_messages with ON CONFLICT (message_id) DO NOTHING.
- Safe to re-run.

Env:
  SIJI_DB_URL — PostgreSQL URL (default matches wa_sync.py)
  SIJI_SQLITE_LEGACY — path to SQLite file (default /opt/siji-dashboard/siji_database.db)
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

import psycopg2

SQLITE_PATH = os.getenv("SIJI_SQLITE_LEGACY", "/opt/siji-dashboard/siji_database.db")
DATABASE_URL = os.getenv(
    "SIJI_DB_URL",
    "postgresql://livin:L1v1n!B1nt4r0_2026@127.0.0.1:5432/livininbintaro",
)
DB_SCHEMA = "siji_bintaro"


def normalize_phone(jid: str) -> str:
    phone = jid.split("@")[0]
    phone = re.sub(r"[^\d]", "", phone)
    if phone.startswith("0"):
        phone = "62" + phone[1:]
    return phone


def is_group_jid(jid: str) -> bool:
    return "@g.us" in jid


def get_conn():
    conn = psycopg2.connect(DATABASE_URL, options=f"-c search_path={DB_SCHEMA},public")
    conn.autocommit = False
    return conn


def main() -> int:
    dry = "--dry-run" in sys.argv
    if not os.path.isfile(SQLITE_PATH):
        print(f"SQLite file not found: {SQLITE_PATH}", file=sys.stderr)
        return 1

    sq = sqlite3.connect(SQLITE_PATH)
    sq.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if dry:
        pg = get_conn()
        pg.autocommit = True
        with pg.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM wa_messages WHERE message_id IS NOT NULL AND trim(message_id) <> ''"
            )
            pg_ids = {r[0] for r in cur.fetchall()}
        pg.close()
        miss = 0
        for row in sq.execute(
            "SELECT message_id FROM wa_messages WHERE message_id IS NOT NULL AND trim(message_id) <> ''"
        ):
            if row[0] not in pg_ids:
                miss += 1
        sq.close()
        print(f"DRY RUN: would insert up to {miss} messages missing by message_id")
        return 0

    pg = get_conn()
    try:
        with pg.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM wa_messages WHERE message_id IS NOT NULL AND trim(message_id) <> ''"
            )
            pg_ids = set(r[0] for r in cur.fetchall())

        to_insert: list[sqlite3.Row] = []
        for row in sq.execute("SELECT * FROM wa_messages"):
            mid = row["message_id"]
            if not mid or not str(mid).strip():
                continue
            if mid in pg_ids:
                continue
            to_insert.append(row)

        conv_needed = {r["conversation_jid"] for r in to_insert}
        conv_rows: dict[str, sqlite3.Row] = {}
        for jid in conv_needed:
            r = sq.execute("SELECT * FROM wa_conversations WHERE jid = ?", (jid,)).fetchone()
            if r:
                conv_rows[jid] = r

        inserted_conv = 0
        with pg.cursor() as cur:
            for jid, cr in conv_rows.items():
                phone = cr["phone"] or normalize_phone(jid)
                is_g = bool(cr["is_group"]) if cr["is_group"] is not None else is_group_jid(jid)
                cur.execute(
                    """
                    INSERT INTO wa_conversations (
                        jid, phone, contact_name, customer_name, last_message, last_message_time,
                        unread_count, is_group, total_messages, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (jid) DO NOTHING
                    """,
                    (
                        cr["jid"],
                        phone,
                        cr["contact_name"],
                        cr["customer_name"],
                        cr["last_message"],
                        cr["last_message_time"],
                        cr["unread_count"] or 0,
                        is_g,
                        cr["total_messages"] or 0,
                        cr["synced_at"] or now,
                    ),
                )
                if cur.rowcount:
                    inserted_conv += 1

            # conversations referenced by messages but missing in SQLite wa_conversations
            missing_conv = conv_needed - set(conv_rows.keys())
            for jid in missing_conv:
                phone = normalize_phone(jid)
                is_g = is_group_jid(jid)
                cur.execute(
                    """
                    INSERT INTO wa_conversations (jid, phone, is_group, synced_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (jid) DO NOTHING
                    """,
                    (jid, phone, is_g, now),
                )
                if cur.rowcount:
                    inserted_conv += 1

        inserted_msg = 0
        with pg.cursor() as cur:
            for r in to_insert:
                cur.execute(
                    """
                    INSERT INTO wa_messages (
                        conversation_jid, message_id, sender_jid, sender_name, message_text,
                        message_type, media_url, is_from_me, is_forwarded, quoted_message_id,
                        timestamp, status, synced_at, is_bot
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (message_id) DO NOTHING
                    """,
                    (
                        r["conversation_jid"],
                        r["message_id"],
                        r["sender_jid"],
                        r["sender_name"],
                        r["message_text"],
                        r["message_type"],
                        r["media_url"],
                        bool(r["is_from_me"]),
                        bool(r["is_forwarded"]) if r["is_forwarded"] is not None else False,
                        r["quoted_message_id"],
                        r["timestamp"],
                        r["status"],
                        r["synced_at"] or now,
                        bool(r["is_bot"]) if r["is_bot"] is not None else False,
                    ),
                )
                if cur.rowcount:
                    inserted_msg += 1

        pg.commit()
        print(
            f"Backfill complete: conversations inserted {inserted_conv}, "
            f"messages inserted {inserted_msg} (skipped existing message_id)"
        )
    except Exception:
        pg.rollback()
        raise
    finally:
        pg.close()
        sq.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
