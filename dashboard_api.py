"""
SIJI Bintaro - Dashboard API v2.3
FastAPI router untuk analytics dashboard
Database: PostgreSQL (livininbintaro DB, schema: siji_bintaro)
Migrated from SQLite - 2026-03-20
Supports: month, year, date_from/date_to filtering
New: SLA alerts, customer search/detail, area analysis, LLM insights
"""

from fastapi import APIRouter, HTTPException, Query
from cache_decorator import cached
from pydantic import BaseModel
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import urllib.request
from datetime import datetime, timedelta
import calendar

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

DB_URL = "postgresql://livin:L1v1n!B1nt4r0_2026@172.17.0.2:5432/livininbintaro"


class DBConn:
    """Wrapper to provide sqlite3-compatible execute() interface over psycopg2"""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params)
        return cur

    def close(self):
        self._conn.close()


def get_db():
    conn = psycopg2.connect(DB_URL, options='-c search_path=siji_bintaro')
    return DBConn(conn)


def get_sla_days(service_name: str) -> int:
    """Return SLA benchmark in days based on service type keyword."""
    if not service_name:
        return 3
    s = service_name.lower()
    if any(k in s for k in ['express', '24jam', '24 jam', 'kilat']):
        return 1
    if any(k in s for k in ['karpet', 'gordyn', 'gordin', 'kasur', 'sofa']):
        return 8
    if any(k in s for k in ['tas', 'bag']):
        return 7
    if any(k in s for k in ['sepatu', 'shoes', 'shoe']):
        return 5
    if any(k in s for k in ['reguler', 'setrika', 'laundry']):
        return 3
    return 3


def resolve_date_range(
    month: Optional[str] = None,
    year: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
):
    """
    Resolve date range from params. Priority: date_from/date_to > year > month.
    Returns (start, end, prev_start, prev_end, label, mode).
    """
    if date_from and date_to:
        start = date_from
        end = date_to
        # Previous period = same duration before start
        from datetime import date as dt_date
        d_from = datetime.strptime(date_from, "%Y-%m-%d")
        d_to = datetime.strptime(date_to, "%Y-%m-%d")
        delta = (d_to - d_from).days
        prev_end_dt = d_from - timedelta(days=1)
        prev_start_dt = prev_end_dt - timedelta(days=delta)
        prev_start = prev_start_dt.strftime("%Y-%m-%d")
        prev_end = prev_end_dt.strftime("%Y-%m-%d")
        label = f"{date_from} s/d {date_to}"
        return start, end, prev_start, prev_end, label, "custom"

    if year:
        yr = int(year)
        start = f"{yr}-01-01"
        end = f"{yr}-12-31"
        prev_start = f"{yr-1}-01-01"
        prev_end = f"{yr-1}-12-31"
        label = str(yr)
        return start, end, prev_start, prev_end, label, "year"

    # Default: monthly
    if not month:
        month = datetime.now().strftime("%Y-%m")
    y, m = month.split("-")
    start = f"{month}-01"
    last_day = calendar.monthrange(int(y), int(m))[1]
    end = f"{month}-{last_day}"

    date_obj = datetime.strptime(month, "%Y-%m")
    if date_obj.month == 1:
        prev_month = f"{date_obj.year - 1}-12"
    else:
        prev_month = f"{date_obj.year}-{date_obj.month - 1:02d}"
    py, pm = prev_month.split("-")
    prev_start = f"{prev_month}-01"
    prev_last_day = calendar.monthrange(int(py), int(pm))[1]
    prev_end = f"{prev_month}-{prev_last_day}"
    label = month
    return start, end, prev_start, prev_end, label, "month"


# Common params for date-filtered endpoints
def date_params():
    """Document the common date query params"""
    pass


# ─── Overview ──────────────────────────────────────────────────────────────────
@cached(ttl=300, prefix="overview")

@router.get("/overview")
async def get_overview(
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    year: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    date_from: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    start, end, prev_start, prev_end, label, mode = resolve_date_range(month, year, date_from, date_to)

    # For annual mode, compute target proportionally
    if mode == "year":
        target_revenue = 40_000_000 * 12
    elif mode == "custom":
        d1 = datetime.strptime(start, "%Y-%m-%d")
        d2 = datetime.strptime(end, "%Y-%m-%d")
        months_span = max(((d2 - d1).days / 30), 1)
        target_revenue = 40_000_000 * months_span
    else:
        target_revenue = 40_000_000

    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT
                COUNT(*) as total_orders,
                COALESCE(SUM(total_tagihan), 0) as revenue,
                COUNT(DISTINCT customer_phone) as active_customers
            FROM transactions
            WHERE date_of_transaction >= %s AND date_of_transaction <= %s
              AND total_tagihan > 0
        """, (start, end))
        current = dict(cur.fetchone())

        cur = conn.execute("""
            SELECT
                COUNT(*) as total_orders,
                COALESCE(SUM(total_tagihan), 0) as revenue
            FROM transactions
            WHERE date_of_transaction >= %s AND date_of_transaction <= %s
              AND total_tagihan > 0
        """, (prev_start, prev_end))
        previous = dict(cur.fetchone())

        cur = conn.execute("""
            SELECT COUNT(DISTINCT customer_phone) as new_customers
            FROM transactions
            WHERE date_of_transaction >= %s AND date_of_transaction <= %s
              AND total_tagihan > 0
              AND customer_phone NOT IN (
                  SELECT DISTINCT customer_phone
                  FROM transactions
                  WHERE date_of_transaction < %s
                    AND customer_phone IS NOT NULL
              )
        """, (start, end, start))
        new_cust = cur.fetchone()["new_customers"] or 0

        cur = conn.execute("""
            SELECT pembayaran, COUNT(*) as count
            FROM transactions
            WHERE date_of_transaction >= %s AND date_of_transaction <= %s
              AND total_tagihan > 0
            GROUP BY pembayaran
        """, (start, end))
        payment_status = {r["pembayaran"] or "Unknown": r["count"] for r in cur.fetchall()}

        cur = conn.execute("""
            SELECT
                CASE
                    WHEN progress_status = '100%%' AND pengambilan = 'Diambil Semua' THEN 'Selesai'
                    WHEN progress_status = '100%%' AND pengambilan != 'Diambil Semua' THEN 'Siap Diambil'
                    ELSE 'Proses'
                END as status,
                COUNT(*) as count
            FROM transactions
            WHERE date_of_transaction >= %s AND date_of_transaction <= %s
              AND total_tagihan > 0
            GROUP BY 1
        """, (start, end))
        order_status = {r["status"]: r["count"] for r in cur.fetchall()}

        revenue_change = 0
        orders_change = 0
        if previous["revenue"] > 0:
            revenue_change = ((current["revenue"] - previous["revenue"]) / previous["revenue"]) * 100
        if previous["total_orders"] > 0:
            orders_change = ((current["total_orders"] - previous["total_orders"]) / previous["total_orders"]) * 100

        target_pct = (current["revenue"] / target_revenue) * 100 if target_revenue > 0 else 0

        prev_label = "vs periode sebelumnya"
        if mode == "month":
            prev_label = "vs bulan lalu"
        elif mode == "year":
            prev_label = "vs tahun lalu"

        # SLA alerts - ongoing orders with overdue detection
        cur = conn.execute("""
            SELECT no_nota, customer_name, nama_layanan, group_layanan,
                date_of_transaction, progress_status,
                EXTRACT(DAY FROM NOW() - date_of_transaction::timestamp)::INTEGER as days_in_progress
            FROM transactions
            WHERE total_tagihan > 0
              AND progress_status != '100%%'
            ORDER BY days_in_progress DESC
        """)
        sla_alerts = []
        total_ongoing = 0
        overdue_count = 0
        critical_count = 0
        for r in cur.fetchall():
            total_ongoing += 1
            service = r["nama_layanan"] or r["group_layanan"] or ""
            sla = get_sla_days(service)
            days = r["days_in_progress"] or 0
            is_overdue = days > sla
            is_critical = days > sla * 2
            if is_overdue:
                overdue_count += 1
            if is_critical:
                critical_count += 1
            if len(sla_alerts) < 10:
                sla_alerts.append({
                    "no_nota": r["no_nota"],
                    "customer": r["customer_name"],
                    "service": service or "-",
                    "days": days,
                    "sla": sla,
                    "overdue": is_overdue,
                    "critical": is_critical
                })
        sla_summary = {
            "total_ongoing": total_ongoing,
            "overdue": overdue_count,
            "critical": critical_count
        }

        return {
            "period": label,
            "mode": mode,
            "date_range": {"start": start, "end": end},
            "revenue": {
                "current": current["revenue"],
                "previous": previous["revenue"],
                "change_pct": round(revenue_change, 1),
                "target": target_revenue,
                "target_pct": round(target_pct, 1)
            },
            "orders": {
                "total": current["total_orders"],
                "previous": previous["total_orders"],
                "change_pct": round(orders_change, 1),
                "by_status": order_status,
                "by_payment": payment_status
            },
            "customers": {
                "active": current["active_customers"],
                "new": new_cust,
                "returning": current["active_customers"] - new_cust
            },
            "comparison_label": prev_label,
            "sla_alerts": sla_alerts,
            "sla_summary": sla_summary
        }
    finally:
        conn.close()


# ─── Revenue ───────────────────────────────────────────────────────────────────

@cached(ttl=300, prefix="revenue_monthly")
@router.get("/revenue/monthly")
async def get_revenue_monthly():
    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT
                to_char(date_of_transaction, 'YYYY-MM') as month,
                COALESCE(SUM(total_tagihan), 0) as revenue,
                COUNT(*) as orders
            FROM transactions
            WHERE total_tagihan > 0
              AND date_of_transaction >= CURRENT_DATE - INTERVAL '72 months'
            GROUP BY 1
            ORDER BY 1
        """)
        data = [{"month": r["month"], "revenue": r["revenue"], "orders": r["orders"]} for r in cur.fetchall()]
        return {"period": "monthly", "data": data}
    finally:
        conn.close()


@cached(ttl=180, prefix="revenue_daily")
@router.get("/revenue/daily")
async def get_revenue_daily(
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    year: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    date_from: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    start, end, _, _, label, mode = resolve_date_range(month, year, date_from, date_to)

    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT
                date_of_transaction as date,
                COALESCE(SUM(total_tagihan), 0) as revenue,
                COUNT(*) as orders
            FROM transactions
            WHERE date_of_transaction >= %s AND date_of_transaction <= %s
              AND total_tagihan > 0
            GROUP BY date_of_transaction
            ORDER BY date_of_transaction
        """, (start, end))
        data = [{"date": str(r["date"]), "revenue": r["revenue"], "orders": r["orders"]} for r in cur.fetchall()]
        return {"period": "daily", "label": label, "data": data}
    finally:
        conn.close()


@router.get("/revenue/by-service")
async def get_revenue_by_service(
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    year: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    date_from: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    start, end, _, _, label, mode = resolve_date_range(month, year, date_from, date_to)

    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT
                COALESCE(group_layanan, nama_layanan, 'Lainnya') as name,
                COALESCE(SUM(total_tagihan), 0) as revenue,
                COUNT(*) as orders
            FROM transactions
            WHERE date_of_transaction >= %s AND date_of_transaction <= %s
              AND total_tagihan > 0
            GROUP BY 1
            ORDER BY revenue DESC
        """, (start, end))
        by_category = [dict(r) for r in cur.fetchall()]

        cur = conn.execute("""
            SELECT
                nama_layanan as name,
                group_layanan as category,
                COALESCE(SUM(total_tagihan), 0) as revenue,
                COUNT(*) as orders
            FROM transactions
            WHERE date_of_transaction >= %s AND date_of_transaction <= %s
              AND total_tagihan > 0
              AND nama_layanan IS NOT NULL AND nama_layanan != ''
            GROUP BY nama_layanan, group_layanan
            ORDER BY revenue DESC
            LIMIT 10
        """, (start, end))
        by_service = [dict(r) for r in cur.fetchall()]

        return {"label": label, "by_category": by_category, "by_service": by_service}
    finally:
        conn.close()


# ─── Orders ───────────────────────────────────────────────────────────────────

@router.get("/orders")
async def get_orders(
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    year: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    date_from: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status: str = "all",
    page: int = 1,
    limit: int = 20,
    search: Optional[str] = None
):
    start, end, _, _, label, mode = resolve_date_range(month, year, date_from, date_to)
    offset = (page - 1) * limit

    conn = get_db()
    try:
        where = "WHERE date_of_transaction >= %s AND date_of_transaction <= %s AND total_tagihan > 0"
        params = [start, end]

        if status == "Lunas":
            where += " AND pembayaran = 'Lunas'"
        elif status == "Belum Lunas":
            where += " AND pembayaran = 'Belum Lunas'"
        elif status == "Proses":
            where += " AND progress_status != '100%%'"
        elif status == "Siap Diambil":
            where += " AND progress_status = '100%%' AND pengambilan != 'Diambil Semua'"

        if search:
            where += " AND (customer_name LIKE %s OR no_nota LIKE %s OR customer_phone LIKE %s)"
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        cur = conn.execute(f"SELECT COUNT(*) as total FROM transactions {where}", params)
        total = cur.fetchone()["total"]

        cur = conn.execute(f"""
            SELECT
                no_nota, customer_name, customer_phone, customer_address,
                date_of_transaction, progress_status, total_tagihan,
                pembayaran, pengambilan, pembuat_nota,
                nama_layanan, group_layanan, jenis
            FROM transactions
            {where}
            ORDER BY date_of_transaction DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])

        orders = []
        for r in cur.fetchall():
            prog = r["progress_status"]
            pickup = r["pengambilan"]
            if prog == "100%" and pickup == "Diambil Semua":
                computed_status = "Selesai"
            elif prog == "100%":
                computed_status = "Siap Diambil"
            else:
                computed_status = "Proses"

            orders.append({
                "no_nota": r["no_nota"],
                "date": str(r["date_of_transaction"]),
                "customer": r["customer_name"],
                "phone": r["customer_phone"],
                "address": r["customer_address"],
                "service": r["nama_layanan"] or r["group_layanan"] or "-",
                "category": r["group_layanan"],
                "amount": r["total_tagihan"],
                "payment": r["pembayaran"],
                "status": computed_status,
                "pickup": r["pengambilan"],
                "type": r["jenis"],
                "kasir": r["pembuat_nota"]
            })

        return {
            "label": label,
            "status_filter": status,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": max((total + limit - 1) // limit, 1),
            "orders": orders
        }
    finally:
        conn.close()


@router.get("/orders/ongoing")
async def get_ongoing_orders():
    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT
                no_nota, customer_name, customer_phone,
                date_of_transaction, progress_status, total_tagihan,
                pembayaran, pengambilan, nama_layanan, group_layanan
            FROM transactions
            WHERE total_tagihan > 0
              AND (progress_status != '100%%' OR pengambilan != 'Diambil Semua')
            ORDER BY date_of_transaction DESC
            LIMIT 20
        """)
        orders = []
        for r in cur.fetchall():
            if r["progress_status"] == "100%":
                status = "Siap Diambil"
            else:
                status = "Proses"
            orders.append({
                "no_nota": r["no_nota"],
                "date": str(r["date_of_transaction"]),
                "customer": r["customer_name"],
                "service": r["nama_layanan"] or r["group_layanan"] or "-",
                "amount": r["total_tagihan"],
                "status": status,
                "payment": r["pembayaran"]
            })
        return {"orders": orders}
    finally:
        conn.close()


# ─── Customers ─────────────────────────────────────────────────────────────────

@router.get("/customers/summary")
async def get_customers_summary():
    conn = get_db()
    try:
        cur = conn.execute("SELECT COUNT(DISTINCT customer_phone) as total FROM transactions WHERE customer_phone IS NOT NULL AND total_tagihan > 0")
        total = cur.fetchone()["total"]

        cur = conn.execute("SELECT COUNT(DISTINCT customer_phone) as active FROM transactions WHERE total_tagihan > 0 AND date_of_transaction >= CURRENT_DATE - INTERVAL '60 days'")
        active = cur.fetchone()["active"]

        cur = conn.execute("SELECT COUNT(*) as hvc FROM (SELECT customer_phone FROM transactions WHERE customer_phone IS NOT NULL AND total_tagihan > 0 GROUP BY customer_phone HAVING COUNT(*) >= 5 OR SUM(total_tagihan) >= 1000000) sub")
        hvc_count = cur.fetchone()["hvc"]

        cur = conn.execute("SELECT COUNT(*) as churn FROM (SELECT customer_phone FROM transactions WHERE customer_phone IS NOT NULL AND total_tagihan > 0 GROUP BY customer_phone HAVING COUNT(*) >= 2 AND EXTRACT(DAY FROM NOW() - MAX(date_of_transaction)::timestamp) >= 60) sub")
        churn_count = cur.fetchone()["churn"]

        cur = conn.execute("""
            SELECT
                CASE WHEN cnt >= 8 THEN 'VIP' WHEN cnt >= 5 THEN 'High-Value' WHEN cnt >= 3 THEN 'Regular' WHEN cnt >= 2 THEN 'Occasional' ELSE 'One-time' END as segment,
                COUNT(*) as count
            FROM (SELECT customer_phone, COUNT(*) as cnt FROM transactions WHERE customer_phone IS NOT NULL AND total_tagihan > 0 GROUP BY customer_phone) sub
            GROUP BY 1
            ORDER BY CASE WHEN MIN(cnt) >= 8 THEN 1 WHEN MIN(cnt) >= 5 THEN 2 WHEN MIN(cnt) >= 3 THEN 3 WHEN MIN(cnt) >= 2 THEN 4 ELSE 5 END
        """)
        segments = [{"segment": r["segment"], "count": r["count"]} for r in cur.fetchall()]

        cur = conn.execute("""
            SELECT
                CASE WHEN cnt = 1 THEN '1 order' WHEN cnt BETWEEN 2 AND 5 THEN '2-5 orders' WHEN cnt BETWEEN 6 AND 10 THEN '6-10 orders' ELSE '10+ orders' END as range_label,
                COUNT(*) as count
            FROM (SELECT customer_phone, COUNT(*) as cnt FROM transactions WHERE customer_phone IS NOT NULL AND total_tagihan > 0 GROUP BY customer_phone) sub
            GROUP BY 1
            ORDER BY MIN(cnt)
        """)
        frequency = [{"range": r["range_label"], "count": r["count"]} for r in cur.fetchall()]

        return {"total": total, "active": active, "hvc": hvc_count, "churn_risk": churn_count, "segments": segments, "frequency": frequency}
    finally:
        conn.close()


@router.get("/customers/top")
async def get_top_customers(limit: int = 20):
    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT customer_name, customer_phone, COUNT(*) as total_orders,
                COALESCE(SUM(total_tagihan), 0) as total_spent, ROUND(AVG(total_tagihan), 0) as avg_order,
                MIN(date_of_transaction) as first_order, MAX(date_of_transaction) as last_order,
                EXTRACT(DAY FROM NOW() - MAX(date_of_transaction)::timestamp)::INTEGER as days_since,
                CASE WHEN COUNT(*) >= 8 THEN 'VIP' WHEN COUNT(*) >= 5 THEN 'High-Value' WHEN COUNT(*) >= 3 THEN 'Regular' WHEN COUNT(*) >= 2 THEN 'Occasional' ELSE 'One-time' END as segment
            FROM transactions WHERE customer_phone IS NOT NULL AND total_tagihan > 0
            GROUP BY customer_phone, customer_name ORDER BY total_spent DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["first_order"] = str(d["first_order"]) if d["first_order"] else None
            d["last_order"] = str(d["last_order"]) if d["last_order"] else None
            result.append(d)
        return {"customers": result}
    finally:
        conn.close()


@router.get("/customers/hvc")
async def get_hvc_customers():
    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT customer_name, customer_phone, COUNT(*) as total_orders,
                COALESCE(SUM(total_tagihan), 0) as total_spent, ROUND(AVG(total_tagihan), 0) as avg_order,
                MAX(date_of_transaction) as last_order,
                EXTRACT(DAY FROM NOW() - MAX(date_of_transaction)::timestamp)::INTEGER as days_since
            FROM transactions WHERE customer_phone IS NOT NULL AND total_tagihan > 0
            GROUP BY customer_phone, customer_name HAVING COUNT(*) >= 5 OR SUM(total_tagihan) >= 1000000
            ORDER BY total_spent DESC
        """)
        rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["last_order"] = str(d["last_order"]) if d["last_order"] else None
            result.append(d)
        return {"hvc": result}
    finally:
        conn.close()


@router.get("/customers/churn-risk")
async def get_churn_risk():
    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT customer_name, customer_phone, COUNT(*) as total_orders,
                COALESCE(SUM(total_tagihan), 0) as total_spent,
                MAX(date_of_transaction) as last_order,
                EXTRACT(DAY FROM NOW() - MAX(date_of_transaction)::timestamp)::INTEGER as days_since,
                CASE
                    WHEN EXTRACT(DAY FROM NOW() - MAX(date_of_transaction)::timestamp) >= 90 THEN 'High'
                    WHEN EXTRACT(DAY FROM NOW() - MAX(date_of_transaction)::timestamp) >= 60 THEN 'Medium'
                    ELSE 'Low'
                END as risk
            FROM transactions WHERE customer_phone IS NOT NULL AND total_tagihan > 0
            GROUP BY customer_phone, customer_name
            HAVING COUNT(*) >= 2 AND EXTRACT(DAY FROM NOW() - MAX(date_of_transaction)::timestamp) >= 30
            ORDER BY days_since DESC
        """)
        rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["last_order"] = str(d["last_order"]) if d["last_order"] else None
            result.append(d)
        return {"churn_risk": result}
    finally:
        conn.close()


@router.get("/customers/frequency")
async def get_customer_frequency():
    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT
                CASE WHEN cnt = 1 THEN '1 order' WHEN cnt BETWEEN 2 AND 3 THEN '2-3 orders' WHEN cnt BETWEEN 4 AND 5 THEN '4-5 orders'
                     WHEN cnt BETWEEN 6 AND 10 THEN '6-10 orders' WHEN cnt BETWEEN 11 AND 20 THEN '11-20 orders' ELSE '20+ orders' END as range_label,
                COUNT(*) as count
            FROM (SELECT customer_phone, COUNT(*) as cnt FROM transactions WHERE customer_phone IS NOT NULL AND total_tagihan > 0 GROUP BY customer_phone) sub
            GROUP BY 1
            ORDER BY MIN(cnt)
        """)
        return {"frequency": [{"range": r["range_label"], "count": r["count"]} for r in cur.fetchall()]}
    finally:
        conn.close()


# ─── Payment Status ────────────────────────────────────────────────────────────

@router.get("/payment-status")
async def get_payment_status(
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    year: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    date_from: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    start, end, _, _, label, mode = resolve_date_range(month, year, date_from, date_to)
    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT pembayaran, COUNT(*) as count, COALESCE(SUM(total_tagihan), 0) as amount
            FROM transactions WHERE date_of_transaction >= %s AND date_of_transaction <= %s AND total_tagihan > 0
            GROUP BY pembayaran
        """, (start, end))
        return {"label": label, "status": [{"label": r["pembayaran"] or "Unknown", "count": r["count"], "amount": r["amount"]} for r in cur.fetchall()]}
    finally:
        conn.close()


# ─── Products ──────────────────────────────────────────────────────────────────

@router.get("/products")
async def get_products(
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    year: Optional[str] = Query(default=None, pattern=r"^\d{4}$"),
    date_from: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    start, end, _, _, label, mode = resolve_date_range(month, year, date_from, date_to)
    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT COALESCE(group_layanan, nama_layanan, 'Lainnya') as name,
                COALESCE(SUM(total_tagihan), 0) as revenue, COUNT(*) as orders
            FROM transactions WHERE date_of_transaction >= %s AND date_of_transaction <= %s AND total_tagihan > 0
            GROUP BY 1 ORDER BY revenue DESC
        """, (start, end))
        by_category = [dict(r) for r in cur.fetchall()]

        cur = conn.execute("""
            SELECT nama_layanan as name, group_layanan as category,
                COALESCE(SUM(total_tagihan), 0) as revenue, COUNT(*) as orders
            FROM transactions WHERE date_of_transaction >= %s AND date_of_transaction <= %s
              AND total_tagihan > 0 AND nama_layanan IS NOT NULL AND nama_layanan != ''
            GROUP BY nama_layanan, group_layanan ORDER BY revenue DESC LIMIT 10
        """, (start, end))
        by_service = [dict(r) for r in cur.fetchall()]

        return {"label": label, "by_category": by_category, "by_service": by_service}
    finally:
        conn.close()


# ─── Locations ─────────────────────────────────────────────────────────────────

@router.get("/locations")
async def get_locations(
    month: Optional[str] = None,
    year: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    start, end, _, _, label, mode = resolve_date_range(month, year, date_from, date_to)
    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT COALESCE(normalized_cluster, normalized_area, customer_address, 'Unknown') as location,
                COUNT(*) as orders, COALESCE(SUM(total_tagihan), 0) as revenue,
                COUNT(DISTINCT customer_phone) as customers
            FROM transactions WHERE date_of_transaction >= %s AND date_of_transaction <= %s AND total_tagihan > 0
            GROUP BY 1 ORDER BY orders DESC LIMIT 20
        """, (start, end))
        return {"label": label, "locations": [dict(r) for r in cur.fetchall()]}
    finally:
        conn.close()


# ─── Customer Search & Detail ─────────────────────────────────────────────────

@router.get("/customers/search")
async def search_customers(
    q: str = "",
    area: Optional[str] = None,
    limit: int = 50
):
    conn = get_db()
    try:
        where_parts = ["customer_phone IS NOT NULL", "total_tagihan > 0"]
        params = []

        if q:
            where_parts.append("(customer_name LIKE %s OR customer_phone LIKE %s OR customer_address LIKE %s)")
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        if area:
            where_parts.append("normalized_area = %s")
            params.append(area)

        where = " AND ".join(where_parts)
        params.append(limit)

        cur = conn.execute(f"""
            SELECT customer_name, customer_phone, customer_address,
                normalized_area, COUNT(*) as total_orders,
                COALESCE(SUM(total_tagihan), 0) as total_spent,
                ROUND(AVG(total_tagihan), 0) as avg_order,
                MAX(date_of_transaction) as last_order,
                MIN(date_of_transaction) as first_order,
                EXTRACT(DAY FROM NOW() - MAX(date_of_transaction)::timestamp)::INTEGER as days_since,
                CASE WHEN COUNT(*) >= 8 THEN 'VIP'
                     WHEN COUNT(*) >= 5 THEN 'High-Value'
                     WHEN COUNT(*) >= 3 THEN 'Regular'
                     WHEN COUNT(*) >= 2 THEN 'Occasional'
                     ELSE 'One-time' END as segment
            FROM transactions
            WHERE {where}
            GROUP BY customer_phone, customer_name, customer_address, normalized_area
            ORDER BY total_spent DESC
            LIMIT %s
        """, params)
        rows = cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["last_order"] = str(d["last_order"]) if d["last_order"] else None
            d["first_order"] = str(d["first_order"]) if d["first_order"] else None
            results.append(d)
        return {"query": q, "area": area, "count": len(results), "results": results}
    finally:
        conn.close()


@router.get("/customers/detail")
async def get_customer_detail(phone: str):
    conn = get_db()
    try:
        # Summary
        cur = conn.execute("""
            SELECT customer_name, customer_phone, customer_address,
                normalized_area, COUNT(*) as total_orders,
                COALESCE(SUM(total_tagihan), 0) as total_spent,
                ROUND(AVG(total_tagihan), 0) as avg_order,
                MAX(date_of_transaction) as last_order,
                MIN(date_of_transaction) as first_order,
                EXTRACT(DAY FROM NOW() - MAX(date_of_transaction)::timestamp)::INTEGER as days_since,
                CASE WHEN COUNT(*) >= 8 THEN 'VIP'
                     WHEN COUNT(*) >= 5 THEN 'High-Value'
                     WHEN COUNT(*) >= 3 THEN 'Regular'
                     WHEN COUNT(*) >= 2 THEN 'Occasional'
                     ELSE 'One-time' END as segment
            FROM transactions
            WHERE customer_phone = %s AND total_tagihan > 0
            GROUP BY customer_phone, customer_name, customer_address, normalized_area
        """, (phone,))
        row = cur.fetchone()
        if not row or not row["customer_name"]:
            raise HTTPException(status_code=404, detail="Customer not found")
        summary = dict(row)
        summary["last_order"] = str(summary["last_order"]) if summary["last_order"] else None
        summary["first_order"] = str(summary["first_order"]) if summary["first_order"] else None

        # Churn risk
        days = summary.get("days_since", 0) or 0
        if summary["total_orders"] >= 2 and days >= 90:
            summary["churn_risk"] = "High"
        elif summary["total_orders"] >= 2 and days >= 60:
            summary["churn_risk"] = "Medium"
        elif summary["total_orders"] >= 2 and days >= 30:
            summary["churn_risk"] = "Low"
        else:
            summary["churn_risk"] = "None"

        # Unpaid
        cur = conn.execute("""
            SELECT COUNT(*) as count, COALESCE(SUM(total_tagihan), 0) as amount
            FROM transactions
            WHERE customer_phone = %s AND pembayaran = 'Belum Lunas' AND total_tagihan > 0
        """, (phone,))
        unpaid = dict(cur.fetchone())
        summary["unpaid"] = unpaid

        # Top services
        cur = conn.execute("""
            SELECT COALESCE(group_layanan, nama_layanan, 'Lainnya') as service,
                COUNT(*) as orders, COALESCE(SUM(total_tagihan), 0) as revenue
            FROM transactions
            WHERE customer_phone = %s AND total_tagihan > 0
            GROUP BY 1 ORDER BY orders DESC LIMIT 5
        """, (phone,))
        top_services = [dict(r) for r in cur.fetchall()]

        # Monthly trend (12m)
        cur = conn.execute("""
            SELECT to_char(date_of_transaction, 'YYYY-MM') as month,
                COUNT(*) as orders, COALESCE(SUM(total_tagihan), 0) as revenue
            FROM transactions
            WHERE customer_phone = %s AND total_tagihan > 0
              AND date_of_transaction >= CURRENT_DATE - INTERVAL '12 months'
            GROUP BY 1 ORDER BY 1
        """, (phone,))
        monthly_trend = [dict(r) for r in cur.fetchall()]

        # Recent orders
        cur = conn.execute("""
            SELECT no_nota, date_of_transaction as date, nama_layanan, group_layanan,
                total_tagihan as amount, pembayaran as payment, progress_status,
                pengambilan, jenis
            FROM transactions
            WHERE customer_phone = %s AND total_tagihan > 0
            ORDER BY date_of_transaction DESC LIMIT 20
        """, (phone,))
        recent_orders_raw = cur.fetchall()
        recent_orders = []
        for r in recent_orders_raw:
            d = dict(r)
            d["date"] = str(d["date"]) if d["date"] else None
            recent_orders.append(d)

        return {
            "summary": summary,
            "top_services": top_services,
            "monthly_trend": monthly_trend,
            "recent_orders": recent_orders
        }
    finally:
        conn.close()


# ─── Area Analysis ────────────────────────────────────────────────────────────

@router.get("/areas/list")
async def get_areas_list():
    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT normalized_area as area, COUNT(*) as orders
            FROM transactions
            WHERE normalized_area IS NOT NULL AND normalized_area != '' AND total_tagihan > 0
            GROUP BY normalized_area ORDER BY orders DESC
        """)
        return {"areas": [dict(r) for r in cur.fetchall()]}
    finally:
        conn.close()


@router.get("/areas/analysis")
async def get_areas_analysis():
    conn = get_db()
    try:
        cur = conn.execute("""
            SELECT normalized_area as area,
                COUNT(*) as orders,
                COUNT(DISTINCT customer_phone) as customers,
                COALESCE(SUM(total_tagihan), 0) as revenue,
                ROUND(AVG(total_tagihan), 0) as avg_order
            FROM transactions
            WHERE normalized_area IS NOT NULL AND normalized_area != '' AND total_tagihan > 0
            GROUP BY normalized_area ORDER BY revenue DESC
        """)
        areas = [dict(r) for r in cur.fetchall()]

        # Compute growth and recent activity per area
        for a in areas:
            area_name = a["area"]
            # Growth: last 3 months vs previous 3 months
            cur2 = conn.execute("""
                SELECT COALESCE(SUM(total_tagihan), 0) as rev
                FROM transactions
                WHERE normalized_area = %s AND total_tagihan > 0
                  AND date_of_transaction >= CURRENT_DATE - INTERVAL '3 months'
            """, (area_name,))
            recent_rev = cur2.fetchone()["rev"]

            cur2 = conn.execute("""
                SELECT COALESCE(SUM(total_tagihan), 0) as rev
                FROM transactions
                WHERE normalized_area = %s AND total_tagihan > 0
                  AND date_of_transaction >= CURRENT_DATE - INTERVAL '6 months'
                  AND date_of_transaction < CURRENT_DATE - INTERVAL '3 months'
            """, (area_name,))
            prev_rev = cur2.fetchone()["rev"]

            a["growth_pct"] = round(((recent_rev - prev_rev) / prev_rev * 100) if prev_rev > 0 else 0, 1)

            # Recent orders (60 days)
            cur2 = conn.execute("""
                SELECT COUNT(*) as cnt FROM transactions
                WHERE normalized_area = %s AND total_tagihan > 0
                  AND date_of_transaction >= CURRENT_DATE - INTERVAL '60 days'
            """, (area_name,))
            a["recent_orders"] = cur2.fetchone()["cnt"]

            # Unpaid
            cur2 = conn.execute("""
                SELECT COALESCE(SUM(total_tagihan), 0) as amount FROM transactions
                WHERE normalized_area = %s AND pembayaran = 'Belum Lunas' AND total_tagihan > 0
            """, (area_name,))
            a["unpaid"] = cur2.fetchone()["amount"]

        return {"areas": areas}
    finally:
        conn.close()


# ─── LLM Insight ──────────────────────────────────────────────────────────────

class LLMRequest(BaseModel):
    context_type: str  # "area" or "customer"
    data: dict


@router.post("/analysis/llm")
async def get_llm_insight(req: LLMRequest):
    if req.context_type == "area":
        system_prompt = (
            "Kamu adalah analis bisnis laundry SIJI Bintaro. "
            "Berikan analisis singkat area ini dalam 4-5 bullet points bahasa Indonesia. "
            "Fokus: potensi revenue, strategi pertumbuhan, dan rekomendasi aksi. "
            "Format: bullet points dengan dash (-). Singkat dan actionable."
        )
        user_prompt = f"Analisis area bisnis laundry:\n{json.dumps(req.data, indent=2)}"
    elif req.context_type == "customer":
        system_prompt = (
            "Kamu adalah CRM specialist laundry SIJI Bintaro. "
            "Berikan insight customer ini dalam 4-5 bullet points bahasa Indonesia. "
            "Fokus: nilai customer, risiko churn, peluang upsell, dan rekomendasi retensi. "
            "Format: bullet points dengan dash (-). Singkat dan actionable."
        )
        user_prompt = f"Analisis profil customer laundry:\n{json.dumps(req.data, indent=2)}"
    else:
        raise HTTPException(status_code=400, detail="context_type must be 'area' or 'customer'")

    try:
        payload = json.dumps({
            "model": "minimax-m2.5:cloud",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False,
            "options": {"temperature": 0.7}
        }).encode("utf-8")

        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(request, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result.get("message", {}).get("content", "")
            return {"insight": content, "model": "minimax-m2.5:cloud", "status": "ok"}

    except Exception as e:
        return {"insight": "Insight AI sedang tidak tersedia. Silakan coba lagi nanti.", "model": "unavailable", "status": "error", "error": str(e)}


# ─── Legacy compatibility ──────────────────────────────────────────────────────

@router.get("/revenue")
async def get_revenue_compat(period: str = "monthly", month: Optional[str] = None):
    if period == "daily":
        return await get_revenue_daily(month)
    return await get_revenue_monthly()

@router.get("/customers")
async def get_customers_compat():
    summary = await get_customers_summary()
    top = await get_top_customers(20)
    return {**summary, "top_customers": top["customers"]}

@router.get("/hvc-churn")
async def get_hvc_churn_compat():
    hvc = await get_hvc_customers()
    churn = await get_churn_risk()
    return {**hvc, **churn}


# ─── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health_check():
    try:
        conn = get_db()
        cur = conn.execute("SELECT COUNT(*) as total FROM transactions")
        total = cur.fetchone()["total"]
        conn.close()
        return {"status": "ok", "database": "postgresql", "transactions": total}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─── Documentation ────────────────────────────────────────────────────────────

from fastapi.responses import PlainTextResponse

DOCS_DIR = "/var/www/sijibintaro/dashboard"

@router.get("/docs/{filename}", response_class=PlainTextResponse)
async def get_doc(filename: str):
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files")
    import os
    filepath = os.path.join(DOCS_DIR, os.path.basename(filename))
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    with open(filepath, "r") as f:
        return f.read()


@router.get("/cache/stats")
async def get_cache_stats():
    """Get cache performance statistics"""
    from cache_manager import cache
    return cache.get_stats()


@router.post("/cache/invalidate")
async def invalidate_cache(prefix: Optional[str] = None):
    """Invalidate cache (admin only - add auth later)"""
    from cache_manager import cache
    cache.invalidate(prefix)
    return {
        "success": True,
        "message": f"Cache invalidated" + (f" for prefix: {prefix}" if prefix else " (all)")
    }
