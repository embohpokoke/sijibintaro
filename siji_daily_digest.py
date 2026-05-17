#!/usr/bin/env python3
"""
SIJI.Bintaro — Daily WA Digest
Kirim ringkasan harian ke Telegram admin
Dijalankan via cron: 20:00 WIB (13:00 UTC)
"""

import sqlite3
import httpx
from datetime import datetime, date

DB_PATH = '/opt/sijibintaro/siji.db'
TELEGRAM_BOT_TOKEN = '8510158455:AAHT5gd5xKtrCtzl3kAXuMVUsyCYTAyacjc'
TELEGRAM_CHAT_ID = '5309429603'
TELEGRAM_URL = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'

def run():
    today = date.today().strftime('%Y-%m-%d')
    today_label = datetime.now().strftime('%d %b %Y')

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Total inbound hari ini
    cur.execute("""
        SELECT COUNT(*) FROM wa_conversations
        WHERE direction='inbound' AND DATE(created_at)=?
    """, (today,))
    total_inbound = cur.fetchone()[0]

    # Total outbound hari ini
    cur.execute("""
        SELECT COUNT(*) FROM wa_conversations
        WHERE direction='outbound' AND DATE(created_at)=?
    """, (today,))
    total_outbound = cur.fetchone()[0]

    # Nomor unik yang kontak hari ini
    cur.execute("""
        SELECT COUNT(DISTINCT sender) FROM wa_conversations
        WHERE direction='inbound' AND DATE(created_at)=?
    """, (today,))
    unique_senders = cur.fetchone()[0]

    # Nomor tidak dikenal
    cur.execute("""
        SELECT COUNT(*) FROM wa_conversations
        WHERE direction='inbound' AND category='unknown' AND DATE(created_at)=?
    """, (today,))
    unknown_count = cur.fetchone()[0]

    # Detail semua pesan inbound hari ini
    cur.execute("""
        SELECT sender, message, created_at, category
        FROM wa_conversations
        WHERE direction='inbound' AND DATE(created_at)=?
        ORDER BY created_at
    """, (today,))
    messages = cur.fetchall()

    conn.close()

    if total_inbound == 0:
        text = (
            f"Ringkasan WA SIJI.Bintaro\n"
            f"{today_label}\n\n"
            f"Tidak ada pesan masuk hari ini."
        )
    else:
        lines = [
            f"Ringkasan WA SIJI.Bintaro",
            f"{today_label}",
            f"",
            f"Pesan masuk : {total_inbound}",
            f"Pengirim    : {unique_senders} nomor",
            f"Tdk dikenal : {unknown_count} nomor",
            f"Pesan keluar: {total_outbound}",
            f"",
            f"--- Detail ---",
        ]
        for row in messages:
            sender, msg, ts, cat = row
            time_str = ts[11:16] if ts else '?'
            msg_short = (msg[:60] + '...') if len(msg) > 60 else msg
            cat_icon = {
                'whitelist': '',
                'unknown': '[?]',
                'harga': '[harga]',
                'job': '[lamaran]',
            }.get(cat or '', '')
            lines.append(f"{time_str} +{sender[-6:]} {cat_icon}: {msg_short}")

        text = '\n'.join(lines)

    resp = httpx.post(TELEGRAM_URL, json={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text
    }, timeout=15)
    print(f"Sent: {resp.status_code} | {total_inbound} pesan | {today_label}")

if __name__ == '__main__':
    run()
