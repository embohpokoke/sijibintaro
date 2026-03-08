"""
customer_context.py — Lookup customer profile dari transactions DB
Dipakai oleh autoreply pipeline untuk personalisasi
"""
import sqlite3
from typing import Optional

TX_DB_PATH = "/opt/siji-dashboard/siji_database.db"


def get_customer_context(phone: str) -> dict:
    """
    Lookup customer dari transactions by phone number.
    Returns dict dengan nama, segment, total_tx, last_tx, last_layanan.
    Selalu return dict (tidak pernah None).
    """
    result = {
        "found": False,
        "nama": "",
        "segment": "Baru",
        "total_transaksi": 0,
        "total_belanja": 0,
        "last_transaksi": "",
        "last_layanan": "",
    }

    if not phone or len(phone) < 8:
        return result

    try:
        conn = sqlite3.connect(TX_DB_PATH, timeout=5)
        cur = conn.execute("""
            SELECT
              customer_name,
              COUNT(DISTINCT no_nota) AS total_tx,
              SUM(total_tagihan) AS total_belanja,
              MAX(date_of_transaction) AS last_tx,
              MAX(nama_layanan) AS last_layanan
            FROM transactions
            WHERE customer_phone = ?
              AND customer_phone IS NOT NULL
              AND LENGTH(customer_phone) > 5
            GROUP BY customer_phone
            LIMIT 1
        """, (phone,))

        row = cur.fetchone()
        conn.close()

        if row and row[0]:
            nama, total_tx, total_belanja, last_tx, last_layanan = row
            segment = "VIP" if total_tx >= 20 else ("Reguler" if total_tx >= 5 else "Baru")
            result.update({
                "found": True,
                "nama": nama or "",
                "segment": segment,
                "total_transaksi": total_tx or 0,
                "total_belanja": int(total_belanja or 0),
                "last_transaksi": (last_tx or "")[:10],
                "last_layanan": last_layanan or "",
            })

    except Exception as e:
        print(f"[CustomerCtx] DB error: {e}")

    return result


def format_customer_greeting(ctx: dict, fallback_name: str = "") -> str:
    """
    Return sapaan berdasarkan profil customer.
    VIP/Reguler: pakai nama dari DB.
    Baru/tidak ditemukan: pakai fallback_name dari WA.
    """
    if ctx["found"] and ctx["nama"]:
        return ctx["nama"]
    return fallback_name or "Kak"
