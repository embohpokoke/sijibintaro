"""
order_tracking_api.py — Public order tracking for SIJI Bintaro
Allows customers to look up their laundry orders by nota number or phone.
No authentication required.
"""

from fastapi import APIRouter, Query
import sqlite3
import re

router = APIRouter(prefix="/api/order", tags=["order-tracking"])

DB_PATH = "/opt/siji-dashboard/siji_database.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_phone(phone) -> str:
    if not phone:
        return ""
    s = re.sub(r"[^\d]", "", str(phone).strip())
    if s.startswith("0"):
        s = "62" + s[1:]
    return s


@router.get("/track")
async def track_order(
    q: str = Query(..., min_length=3, max_length=50, description="Nota number or phone number"),
):
    """Public endpoint for customers to track their laundry orders."""
    conn = get_db()
    try:
        q_clean = q.strip()

        # Detect if query is a phone number (starts with 0, 62, or +62)
        is_phone = bool(re.match(r'^(\+?62|0)\d{8,}$', re.sub(r'[\s\-]', '', q_clean)))

        if is_phone:
            phone = normalize_phone(q_clean)
            rows = conn.execute("""
                SELECT no_nota, date_of_transaction, customer_name, nama_layanan,
                       total_tagihan, pembayaran, progress_status,
                       pengambilan, jenis
                FROM transactions
                WHERE REPLACE(REPLACE(REPLACE(customer_phone,'+',''),'-',''),' ','') = ?
                   OR (customer_phone LIKE '0%'
                       AND '62' || SUBSTR(REPLACE(REPLACE(REPLACE(customer_phone,'+',''),'-',''),' ',''),2) = ?)
                ORDER BY date_of_transaction DESC
                LIMIT 20
            """, (phone, phone)).fetchall()
        else:
            # Search by nota number or customer name
            rows = conn.execute("""
                SELECT no_nota, date_of_transaction, customer_name, nama_layanan,
                       total_tagihan, pembayaran, progress_status,
                       pengambilan, jenis
                FROM transactions
                WHERE no_nota LIKE ? OR LOWER(customer_name) LIKE LOWER(?)
                ORDER BY date_of_transaction DESC
                LIMIT 20
            """, (f"%{q_clean}%", f"%{q_clean}%")).fetchall()

        orders = []
        for r in rows:
            progress = r["progress_status"] or ""
            pickup = r["pengambilan"] or ""
            # Determine display status
            if "Diambil" in pickup and pickup != "Belum Diambil":
                display_status = "Selesai"
                status_color = "green"
            elif progress == "100%":
                display_status = "Siap Diambil"
                status_color = "blue"
            elif progress and progress not in ("0%", ""):
                display_status = "Proses"
                status_color = "orange"
            else:
                display_status = "Diterima"
                status_color = "gold"

            orders.append({
                "nota": r["no_nota"],
                "date": r["date_of_transaction"],
                "customer": r["customer_name"],
                "service": r["nama_layanan"],
                "amount": r["total_tagihan"],
                "payment": r["pembayaran"],
                "status": display_status,
                "status_color": status_color,
                "pickup": pickup,
                "type": r["jenis"],
            })

        return {"orders": orders, "count": len(orders), "query": q_clean}
    finally:
        conn.close()
