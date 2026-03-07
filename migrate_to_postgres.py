"""
SIJI Data Migration: SQLite → PostgreSQL
Migrates from:
  - Source A: siji_database.db (13,089 tx, Feb 2021 - Jan 2026)
  - Source B: siji_customers.db (2,176 customers + 332 tx Jan-Feb 2026)
To: PostgreSQL siji_db on Docker (localhost:5432)
"""

import sqlite3
import psycopg2
import sys
import re
from datetime import datetime

# Import product mapping
sys.path.insert(0, '/root/sijibintaro-api')
from product_mapping import classify_product

# Config
PG_HOST = "localhost"  # Will be run via SSH tunnel or on VPS
PG_PORT = 5432
PG_DB = "siji_db"
PG_USER = "siji"
PG_PASS = "siji2026db"

DB_A = "/root/sijibintaro-api/siji_database_full.db"
DB_B = "/root/sijibintaro-api/siji_customers.db"

def parse_indonesian_date(date_str):
    """Parse dates like '4 Feb 2026 17:58' or '2025-03-06' to YYYY-MM-DD"""
    if not date_str or date_str == '-':
        return None
    
    # Already YYYY-MM-DD
    if re.match(r'^\d{4}-\d{2}-\d{2}', date_str):
        return date_str[:10]
    
    months = {
        'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
        'mei': '05', 'may': '05', 'jun': '06', 'jul': '07',
        'agu': '08', 'aug': '08', 'sep': '09', 'okt': '10',
        'oct': '10', 'nov': '11', 'des': '12', 'dec': '12'
    }
    
    m = re.match(r'(\d{1,2})\s+(\w{3})\s+(\d{4})', date_str)
    if m:
        day, mon, year = m.groups()
        mon_num = months.get(mon.lower()[:3])
        if mon_num:
            return f"{year}-{mon_num}-{int(day):02d}"
    
    return None

def normalize_phone(phone):
    """Normalize phone to 62xxx format"""
    if not phone:
        return None
    phone = re.sub(r'[^\d]', '', str(phone))
    if phone.startswith('0'):
        phone = '62' + phone[1:]
    if not phone.startswith('62'):
        phone = '62' + phone
    return phone

def migrate():
    # Connect to PostgreSQL via SSH tunnel
    # Run this script on VPS or set up SSH tunnel first
    pg = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS)
    pg.autocommit = False
    cur = pg.cursor()
    
    print("=== Step 1: Insert Product Categories & Mappings ===")
    
    # Collect all unique products from both sources
    products = set()
    conn_a = sqlite3.connect(DB_A)
    for row in conn_a.execute("SELECT DISTINCT nama_layanan FROM transactions WHERE nama_layanan IS NOT NULL"):
        products.add(row[0])
    conn_a.close()
    
    conn_b = sqlite3.connect(DB_B)
    for row in conn_b.execute("SELECT DISTINCT nama_layanan FROM transactions WHERE nama_layanan IS NOT NULL AND nama_layanan NOT IN ('Belum Diambil','Diambil Semua','Diambil')"):
        products.add(row[0])
    conn_b.close()
    
    # Insert categories
    cat_map = {}  # (kategori, sub) -> id
    mapping_map = {}  # smartlink_name -> (mapping_id, kategori, sub, speed, is_promo)
    
    for prod_name in sorted(products):
        cls = classify_product(prod_name)
        
        cat_key = (cls['kategori'], cls['sub_kategori'])
        if cat_key not in cat_map:
            cur.execute(
                "INSERT INTO product_categories (kategori, sub_kategori) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING id",
                cat_key
            )
            row = cur.fetchone()
            if row:
                cat_map[cat_key] = row[0]
            else:
                cur.execute("SELECT id FROM product_categories WHERE kategori=%s AND sub_kategori=%s", cat_key)
                cat_map[cat_key] = cur.fetchone()[0]
        
        cat_id = cat_map[cat_key]
        cur.execute(
            """INSERT INTO product_mappings (smartlink_name, category_id, speed_tier, is_promo, promo_name, base_product)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id""",
            (prod_name, cat_id, cls['speed_tier'], cls['is_promo'], cls['promo_name'], cls['base_product'])
        )
        row = cur.fetchone()
        if row:
            mapping_map[prod_name] = (row[0], cls['kategori'], cls['sub_kategori'], cls['speed_tier'], cls['is_promo'])
        else:
            cur.execute("SELECT id FROM product_mappings WHERE smartlink_name=%s", (prod_name,))
            mid = cur.fetchone()[0]
            mapping_map[prod_name] = (mid, cls['kategori'], cls['sub_kategori'], cls['speed_tier'], cls['is_promo'])
    
    pg.commit()
    print(f"  Categories: {len(cat_map)}, Mappings: {len(mapping_map)}")
    
    print("\n=== Step 2: Import Customers from Source B ===")
    
    conn_b = sqlite3.connect(DB_B)
    conn_b.row_factory = sqlite3.Row
    customers = conn_b.execute("SELECT * FROM customers").fetchall()
    
    cust_count = 0
    cust_phone_map = {}  # phone -> pg customer id
    cust_name_map = {}   # normalized name -> pg customer id
    
    for c in customers:
        terdaftar = parse_indonesian_date(c['terdaftar_sejak'])
        phone = normalize_phone(c['nomor_telpon'])
        
        cur.execute(
            """INSERT INTO customers (smartlink_id, nama, sapaan, nomor_telpon, jenis_kelamin, 
               tanggal_lahir, agama, instansi, alamat, outlet, terdaftar_sejak, 
               saldo_epayment, deposit_nama, deposit_kuota, deposit_masa_aktif, deposit_sisa_nominal)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (smartlink_id) DO NOTHING RETURNING id""",
            (c['smartlink_id'], c['nama'], c['sapaan'], phone, c['jenis_kelamin'],
             parse_indonesian_date(c['tanggal_lahir']), c['agama'], c['instansi'], c['alamat'],
             c['outlet'], terdaftar, c['saldo_epayment'], c['deposit_nama'],
             c['deposit_kuota'], None, None)
        )
        row = cur.fetchone()
        if row:
            cust_id = row[0]
            cust_count += 1
        else:
            cur.execute("SELECT id FROM customers WHERE smartlink_id=%s", (c['smartlink_id'],))
            cust_id = cur.fetchone()[0]
        
        if phone:
            cust_phone_map[phone] = cust_id
        if c['nama']:
            cust_name_map[c['nama'].strip().lower()] = cust_id
    
    conn_b.close()
    pg.commit()
    print(f"  Imported {cust_count} customers")
    
    print("\n=== Step 3: Import Transactions from Source A (13,089 tx) ===")
    
    conn_a = sqlite3.connect(DB_A)
    conn_a.row_factory = sqlite3.Row
    
    tx_count = 0
    skip_count = 0
    batch = []
    
    for t in conn_a.execute("SELECT * FROM transactions ORDER BY date_of_transaction"):
        nama = t['nama_layanan']
        mapping = mapping_map.get(nama)
        
        # Match customer
        phone = normalize_phone(t['customer_phone'])
        cust_id = None
        if phone:
            cust_id = cust_phone_map.get(phone)
        if not cust_id and t['customer_name']:
            cust_id = cust_name_map.get(t['customer_name'].strip().lower())
        
        tgl_terima = t['date_of_transaction']  # Already YYYY-MM-DD
        tgl_selesai = parse_indonesian_date(t['tgl_selesai'])
        tgl_pengambilan = parse_indonesian_date(t['tgl_pengambilan'])
        
        batch.append((
            t['no_nota'], cust_id, t['customer_name'], phone, t['customer_address'],
            t['progress_status'], t['outlet'] or 'SIJI Bintaro',
            tgl_terima, tgl_selesai, tgl_pengambilan,
            t['subtotal'] or 0, t['tambahan_express'] or 0, t['diskon'] or 0,
            t['pajak'] or 0, 0,  # biaya_service
            t['total_tagihan'],
            t['jenis'] or 'Reguler', t['pembayaran'], t['pengambilan'],
            t['pembuat_nota'], t['keterangan_nota'],
            nama,
            mapping[0] if mapping else None,
            mapping[1] if mapping else None,
            mapping[2] if mapping else None,
            mapping[3] if mapping else 'REGULER',
            mapping[4] if mapping else False,
            t['jumlah'], t['satuan'], None, t['jumlah_item'],
            t['keterangan_layanan'],
            'siji_database'
        ))
        
        if len(batch) >= 500:
            _insert_batch(cur, batch)
            tx_count += len(batch)
            batch = []
    
    if batch:
        _insert_batch(cur, batch)
        tx_count += len(batch)
    
    conn_a.close()
    pg.commit()
    print(f"  Imported {tx_count} transactions from Source A")
    
    print("\n=== Step 4: Import NEW transactions from Source B (Feb 2026) ===")
    
    conn_b = sqlite3.connect(DB_B)
    conn_b.row_factory = sqlite3.Row
    
    new_tx = 0
    skip_tx = 0
    
    for t in conn_b.execute("SELECT * FROM transactions ORDER BY tgl_terima"):
        nama = t['nama_layanan']
        
        # Skip column-shifted records
        if nama in ('Belum Diambil', 'Diambil Semua', 'Diambil', None):
            skip_tx += 1
            continue
        
        tgl_terima = parse_indonesian_date(t['tgl_terima'])
        if not tgl_terima:
            skip_tx += 1
            continue
        
        mapping = mapping_map.get(nama)
        phone = normalize_phone(t['customer_phone'])
        cust_id = None
        if phone:
            cust_id = cust_phone_map.get(phone)
        if not cust_id and t['customer_name']:
            cust_id = cust_name_map.get(t['customer_name'].strip().lower())
        
        tgl_selesai = parse_indonesian_date(t['tgl_selesai'])
        
        try:
            cur.execute(
                """INSERT INTO transactions 
                   (no_nota, customer_id, customer_name, customer_phone, customer_address,
                    progress_status, outlet, tgl_terima, tgl_selesai, tgl_pengambilan,
                    subtotal, tambahan_express, diskon, pajak, biaya_service, total_tagihan,
                    jenis, pembayaran, pengambilan, kasir, catatan,
                    nama_layanan_original, product_mapping_id, kategori, sub_kategori,
                    speed_tier, is_promo, qty, satuan, harga_item, jumlah_item,
                    keterangan, import_source)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (no_nota) DO NOTHING""",
                (t['no_nota'], cust_id, t['customer_name'], phone, t['customer_address'],
                 t['progress'], t['outlet'] or 'SIJI Bintaro',
                 tgl_terima, tgl_selesai, None,
                 t['subtotal'] or 0, t['tambahan_express'] or 0, t['diskon'] or 0,
                 t['pajak'] or 0, t['biaya_service'] or 0, t['total_tagihan'],
                 t['jenis_transaksi'] or 'Reguler', t['status_bayar'], t['status_ambil'],
                 t['kasir'], t['catatan'],
                 nama,
                 mapping[0] if mapping else None,
                 mapping[1] if mapping else None,
                 mapping[2] if mapping else None,
                 mapping[3] if mapping else 'REGULER',
                 mapping[4] if mapping else False,
                 t['qty'], t['satuan'], t['harga'], t['jumlah'],
                 t['keterangan'],
                 'siji_customers')
            )
            if cur.rowcount > 0:
                new_tx += 1
        except Exception as e:
            print(f"  Error on {t['no_nota']}: {e}")
            pg.rollback()
    
    conn_b.close()
    pg.commit()
    print(f"  New transactions: {new_tx}, Skipped: {skip_tx}")
    
    print("\n=== Step 5: Validation ===")
    cur.execute("SELECT COUNT(*) FROM customers")
    print(f"  Total customers: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM transactions")
    print(f"  Total transactions: {cur.fetchone()[0]}")
    cur.execute("SELECT SUM(total_tagihan) FROM transactions")
    print(f"  Total revenue: {cur.fetchone()[0]:,.0f}")
    cur.execute("SELECT MIN(tgl_terima), MAX(tgl_terima) FROM transactions")
    row = cur.fetchone()
    print(f"  Date range: {row[0]} to {row[1]}")
    cur.execute("SELECT kategori, COUNT(*) FROM transactions GROUP BY kategori ORDER BY COUNT(*) DESC")
    print(f"  Category breakdown:")
    for row in cur.fetchall():
        print(f"    {row[0]}: {row[1]}")
    cur.execute("SELECT COUNT(*) FROM transactions WHERE customer_id IS NOT NULL")
    linked = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM transactions")
    total = cur.fetchone()[0]
    print(f"  Customer linkage: {linked}/{total} ({linked/total*100:.1f}%)")
    
    pg.close()
    print("\n✅ Migration complete!")


def _insert_batch(cur, batch):
    for b in batch:
        try:
            cur.execute(
                """INSERT INTO transactions 
                   (no_nota, customer_id, customer_name, customer_phone, customer_address,
                    progress_status, outlet, tgl_terima, tgl_selesai, tgl_pengambilan,
                    subtotal, tambahan_express, diskon, pajak, biaya_service, total_tagihan,
                    jenis, pembayaran, pengambilan, kasir, catatan,
                    nama_layanan_original, product_mapping_id, kategori, sub_kategori,
                    speed_tier, is_promo, qty, satuan, harga_item, jumlah_item,
                    keterangan, import_source)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (no_nota) DO NOTHING""",
                b
            )
        except Exception as e:
            print(f"  Error: {e} on nota {b[0]}")


if __name__ == "__main__":
    migrate()
