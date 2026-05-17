#!/usr/bin/env python3.12
"""
SIJI Rekon Sync — Direct DB sync untuk invoice + payment signals.
Bisa di-call via cron tanpa auth (direct DB access).
"""
import os, sys, asyncio
from datetime import datetime, timezone

sys.path.insert(0, '/opt/sijibintaro')

from database import get_db_dict
from invoice_parser import (
    detect_payment_text,
    find_local_media,
    iter_invoice_messages,
    iter_payment_images,
    parse_invoice_text,
)

GOWA_DB = "/opt/siji-dashboard/siji_database.db"


def _latest_synced_ts(conn):
    cur = conn.cursor()
    cur.execute("SELECT MAX(wa_timestamp) FROM invoice_rekon")
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def _latest_signal_ts(conn):
    cur = conn.cursor()
    cur.execute("SELECT MAX(wa_timestamp) FROM payment_signal")
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def _upsert_invoice(conn, jid, phone, ts_str, msg, parsed):
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
            parsed["nota_number"], jid, phone, parsed["customer_name"],
            parsed["total_tagihan"], parsed.get("grand_total"),
            parsed["status_invoice"], parsed.get("layanan"),
            parsed.get("terima_at"), parsed.get("selesai_at"),
            ts_str, msg.get("message_text", "")[:4000],
        ),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _upsert_signal(conn, jid, msg, payload, local_path):
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
            jid, msg.get("message_id"), msg["timestamp"],
            payload.get("signal_type", "text_caption"),
            payload.get("is_payment", False),
            payload.get("bank_name"), payload.get("amount"),
            payload.get("reference"), msg.get("message_text", "")[:500],
            local_path, payload.get("confidence", 0.0),
        ),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _try_auto_match(conn, invoice_id, total, jid, inv_ts):
    cur = conn.cursor()
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
    sig_id = signal[0]
    cur.execute(
        "UPDATE payment_signal SET invoice_rekon_id=%s, rekon_status='auto_matched', rekon_at=NOW() WHERE id=%s",
        (invoice_id, sig_id),
    )
    cur.execute(
        "UPDATE invoice_rekon SET payment_signal_id=%s, rekon_status='auto_matched', rekon_at=NOW() WHERE id=%s",
        (sig_id, invoice_id),
    )


def _sync_invoices(conn, since_ts=None):
    import sqlite3 as _sqlite3
    stats = {"invoices_new": 0, "invoices_updated": 0}
    gowa_conn = _sqlite3.connect(GOWA_DB)
    gowa_conn.row_factory = _sqlite3.Row
    try:
        where = "message_text LIKE '%FAKTUR ELEKTRONIK%' AND is_from_me = 1"
        params = []
        if since_ts:
            where += " AND timestamp > ?"
            params.append(since_ts)
        rows = gowa_conn.execute(
            f"SELECT * FROM wa_messages WHERE {where} ORDER BY timestamp ASC",
            params,
        ).fetchall()
        for row in rows:
            msg = dict(row)
            parsed = parse_invoice_text(msg.get("message_text", ""))
            if not parsed:
                continue
            jid = msg["conversation_jid"]
            phone = jid.split("@")[0] if "@" in jid else jid
            inv_id = _upsert_invoice(conn, jid, phone, msg["timestamp"], msg, parsed)
            if inv_id:
                stats["invoices_new"] += 1
                _try_auto_match(conn, inv_id, parsed["total_tagihan"], jid, msg["timestamp"])
    finally:
        gowa_conn.close()
    return stats


def _sync_signals(conn, since_ts=None, jid_filter=None):
    import sqlite3 as _sqlite3
    stats = {"signals_new": 0, "signals_skipped": 0}
    gowa_conn = _sqlite3.connect(GOWA_DB)
    gowa_conn.row_factory = _sqlite3.Row
    try:
        where_parts = ["is_from_me = 0", "(message_type = 'image' OR (message_type = 'text' AND message_text LIKE '%Rp%'))"]
        params = []
        if since_ts:
            where_parts.append("timestamp > ?")
            params.append(since_ts)
        if jid_filter:
            placeholders = ",".join("?" * len(jid_filter))
            where_parts.append(f"conversation_jid IN ({placeholders})")
            params.extend(jid_filter)
        rows = gowa_conn.execute(
            f"SELECT * FROM wa_messages WHERE {' AND '.join(where_parts)} ORDER BY timestamp ASC",
            params,
        ).fetchall()
        for row in rows:
            msg = dict(row)
            caption = msg.get("message_text", "")
            msg_type = msg.get("message_type", "text")
            jid = msg["conversation_jid"]

            payment = detect_payment_text(caption)
            if payment:
                payment["signal_type"] = "text_caption" if msg_type == "image" else "text_only"
            elif msg_type == "image" and not caption.startswith("[image:"):
                payment = {"is_payment": False, "signal_type": "text_caption", "confidence": 0.0}
            elif msg_type == "image":
                payment = {"is_payment": False, "signal_type": "vision_ocr", "confidence": 0.0}
            else:
                stats["signals_skipped"] += 1
                continue

            local_path = find_local_media(msg["timestamp"]) if msg_type == "image" else None
            sig_id = _upsert_signal(conn, jid, msg, payment, local_path)
            if sig_id:
                stats["signals_new"] += 1
                if payment.get("is_payment") and payment.get("amount"):
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
                            "UPDATE payment_signal SET invoice_rekon_id=%s, rekon_status='auto_matched', rekon_at=NOW() WHERE id=%s",
                            (inv[0], sig_id),
                        )
                        cur.execute(
                            "UPDATE invoice_rekon SET payment_signal_id=%s, rekon_status='auto_matched', rekon_at=NOW(), updated_at=NOW() WHERE id=%s",
                            (sig_id, inv[0]),
                        )
    finally:
        gowa_conn.close()
    return stats


def main(full=False):
    print(f"[{datetime.now(timezone.utc)}] Rekon sync starting...")
    with get_db_dict() as conn:
        since_inv = None if full else _latest_synced_ts(conn)
        since_sig = None if full else _latest_signal_ts(conn)

        cur = conn.cursor()
        cur.execute("SELECT DISTINCT conversation_jid FROM invoice_rekon")
        known_jids = {r[0] for r in cur.fetchall()}

        inv_stats = _sync_invoices(conn, since_inv)

        cur.execute("SELECT DISTINCT conversation_jid FROM invoice_rekon")
        all_jids = {r[0] for r in cur.fetchall()}

        sig_stats = _sync_signals(conn, since_sig, all_jids if all_jids else None)

    print(f"  Invoices: {inv_stats}")
    print(f"  Signals:  {sig_stats}")
    print(f"[{datetime.now(timezone.utc)}] Done.")
    return {**inv_stats, **sig_stats}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Re-process all history")
    args = parser.parse_args()
    main(full=args.full)
