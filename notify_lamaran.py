#!/usr/bin/env python3
"""
notify_lamaran.py — Notifikasi & summary lamaran SIJI Bintaro
Modes:
  python3 notify_lamaran.py --new       # Notif real-time lamaran baru (belum ternotif)
  python3 notify_lamaran.py --daily     # Summary harian (untuk cron pagi)
  python3 notify_lamaran.py --test      # Test tanpa kirim
"""

import os, sys, sqlite3, subprocess, argparse, logging
from datetime import datetime, timedelta

# ─── Config ───────────────────────────────────────────────────────────────────
DB_PATH     = "/root/sijibintaro.id/api/siji.db"
ADMIN_TOKEN = os.getenv("SIJI_ADMIN_TOKEN", "sijiadmin2026")
API_BASE    = "http://127.0.0.1:8002"

NOTIFY_TARGETS = [
    ("whatsapp", "+628118606999", "Ocha SIJI"),
    ("whatsapp", "+62811319003",  "Erik"),
]

LOG_FILE = "/var/log/sijibintaro/notify_lamaran.log"
os.makedirs("/var/log/sijibintaro", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("notify_lamaran")

# ─── DB ───────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_new_lamaran():
    """Ambil lamaran yang belum ternotif"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nama, whatsapp, posisi, domisili, pengalaman, created_at
            FROM lamaran WHERE notified = 0 ORDER BY created_at ASC
        """)
        return cur.fetchall()

def get_daily_summary():
    """Ambil summary lamaran 24 jam terakhir"""
    since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nama, whatsapp, posisi, domisili, pengalaman, created_at
            FROM lamaran WHERE created_at >= ? ORDER BY created_at ASC
        """, (since,))
        rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM lamaran WHERE status='baru'")
        total_baru = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM lamaran")
        total_all = cur.fetchone()[0]
        return rows, total_baru, total_all

def mark_notified(ids):
    with get_db() as conn:
        cur = conn.cursor()
        cur.executemany("UPDATE lamaran SET notified=1 WHERE id=?", [(i,) for i in ids])
        conn.commit()

# ─── Messaging ────────────────────────────────────────────────────────────────
def send_wa(target, message, test=False):
    """Kirim pesan via openclaw CLI"""
    if test:
        log.info(f"[TEST] → {target}: {message[:80]}...")
        return True
    try:
        result = subprocess.run(
            ["openclaw", "message", "send",
             "--channel", "whatsapp",
             "-t", target,
             "-m", message],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            log.info(f"✅ Sent to {target}")
            return True
        else:
            log.error(f"❌ Failed to {target}: {result.stderr[:100]}")
            return False
    except Exception as e:
        log.error(f"❌ Exception sending to {target}: {e}")
        return False

# ─── Message Builders ──────────────────────────────────────────────────────────
def build_new_notif(lam):
    """Pesan notif real-time 1 lamaran baru"""
    pengalaman = lam['pengalaman'] or "-"
    if len(pengalaman) > 100:
        pengalaman = pengalaman[:100] + "..."
    return (
        f"🔔 *Lamaran Baru Masuk!*\n\n"
        f"👤 *Nama:* {lam['nama']}\n"
        f"📱 *WA:* {lam['whatsapp']}\n"
        f"📍 *Domisili:* {lam['domisili'] or '-'}\n"
        f"💼 *Posisi:* {lam['posisi']}\n"
        f"📝 *Pengalaman:* {pengalaman}\n"
        f"🕐 *Waktu:* {lam['created_at'][:16]}\n\n"
        f"Cek semua lamaran: https://sijibintaro.id/admin"
    )

def build_daily_summary(rows, total_baru, total_all):
    """Pesan summary harian"""
    today = datetime.now().strftime("%d %b %Y")
    if not rows:
        return (
            f"📋 *Daily Summary Lamaran — {today}*\n\n"
            f"Tidak ada lamaran baru masuk dalam 24 jam terakhir.\n\n"
            f"Total pending review: *{total_baru} lamaran*\n"
            f"Total all-time: {total_all}"
        )

    # Group by posisi
    posisi_count = {}
    for r in rows:
        posisi_count[r['posisi']] = posisi_count.get(r['posisi'], 0) + 1

    lines = [f"📋 *Daily Summary Lamaran — {today}*\n"]
    lines.append(f"✅ *{len(rows)} lamaran baru* dalam 24 jam terakhir:\n")

    for r in rows:
        pengalaman = (r['pengalaman'] or "-")[:60]
        lines.append(
            f"• *{r['nama']}* | {r['posisi']}\n"
            f"  📱 {r['whatsapp']} | 📍 {r['domisili'] or '-'}\n"
            f"  📝 {pengalaman}\n"
        )

    lines.append(f"\n📊 Per posisi:")
    for posisi, cnt in sorted(posisi_count.items(), key=lambda x: -x[1]):
        lines.append(f"  • {posisi}: {cnt}")

    lines.append(f"\n⏳ Total pending review: *{total_baru} lamaran*")
    lines.append(f"📁 Total all-time: {total_all}")
    lines.append(f"\n🔗 https://sijibintaro.id/admin")

    return "\n".join(lines)

# ─── Main ─────────────────────────────────────────────────────────────────────
def run_new(test=False):
    """Notif real-time lamaran baru"""
    rows = get_new_lamaran()
    if not rows:
        log.info("Tidak ada lamaran baru.")
        return

    log.info(f"Found {len(rows)} lamaran baru, mengirim notifikasi...")
    all_ok = True
    for lam in rows:
        msg = build_new_notif(lam)
        for channel, target, label in NOTIFY_TARGETS:
            ok = send_wa(target, msg, test=test)
            if not ok:
                all_ok = False

    if all_ok and not test:
        mark_notified([r['id'] for r in rows])
        log.info(f"Marked {len(rows)} lamaran as notified.")

def run_daily(test=False):
    """Summary harian"""
    rows, total_baru, total_all = get_daily_summary()
    msg = build_daily_summary(rows, total_baru, total_all)
    log.info(f"Sending daily summary ({len(rows)} new lamaran)...")
    for channel, target, label in NOTIFY_TARGETS:
        send_wa(target, msg, test=test)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--new",   action="store_true", help="Notif lamaran baru (unnotified)")
    parser.add_argument("--daily", action="store_true", help="Daily summary (24h)")
    parser.add_argument("--test",  action="store_true", help="Dry run, tidak kirim")
    args = parser.parse_args()

    if args.new:
        run_new(test=args.test)
    elif args.daily:
        run_daily(test=args.test)
    else:
        parser.print_help()
