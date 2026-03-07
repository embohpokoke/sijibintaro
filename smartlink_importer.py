#!/usr/bin/env python3
"""
SIJI Smartlink Import Pipeline
Import Smartlink XLSX exports into PostgreSQL siji_db.

XLSX Structure (Smartlink "Rekap Data Transaksi Reguler"):
  Rows 1-23: Summary/metadata
  Row 24: Header (No, No Nota, Customer, No Telp, Alamat, etc.)
  Row 25: Empty (sometimes)
  Row 26+: Data rows (merged cells for multi-item transactions)
  
  Cols 1-21: Transaction header
  Cols 22-28: Detail items (Tipe, Nama Layanan, Qty, Satuan, Harga, Jumlah, Keterangan)
"""

import os, sys, re, json, argparse
from datetime import datetime

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

from product_mapping import classify_product
from address_normalizer import LocationNormalizer

MONTHS = {
    'jan':'01','feb':'02','mar':'03','apr':'04','mei':'05','may':'05',
    'jun':'06','jul':'07','agu':'08','aug':'08','ags':'08','agus':'08',
    'sep':'09','sept':'09','okt':'10','oct':'10','nov':'11',
    'nop':'11','des':'12','dec':'12'
}

def parse_indo_date(s):
    if not s or str(s).strip() in ('-','','None'): return None
    s = str(s).strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}', s): return s[:10]
    m = re.match(r'(\d{1,2})\s+(\w+)\s+(\d{4})', s)
    if m:
        day, mon, year = m.groups()
        mn = MONTHS.get(mon.lower()[:4]) or MONTHS.get(mon.lower()[:3])
        if mn: return f"{year}-{mn}-{int(day):02d}"
    return None

def normalize_phone(phone):
    if not phone: return None
    p = re.sub(r'[^\d]', '', str(phone))
    if not p: return None
    if p.startswith('0'): p = '62' + p[1:]
    if not p.startswith('62'): p = '62' + p
    return p

def safe_float(v, default=0.0):
    try: return float(v) if v else default
    except: return default


class SmartlinkImporter:
    def __init__(self, pg_conn, normalizer=None):
        self.pg = pg_conn
        self.normalizer = normalizer
        self.stats = {'total':0,'parsed':0,'inserted':0,'updated':0,'duplicate':0,'failed':0,'skipped':0}
        self.errors = []

    def import_file(self, filepath, dry_run=False, normalize_addr=True):
        rows = self._parse_xlsx(filepath)
        self.stats['total'] = len(rows)
        print(f"Parsed {len(rows)} rows from {os.path.basename(filepath)}")
        
        if not rows:
            print("No data!"); return self.stats

        valid = []
        for i, row in enumerate(rows):
            issues = self._validate(row)
            if issues:
                if 'SKIP_SUBITEM' in issues:
                    pass  # Sub-item of multi-item tx, silently skip
                else:
                    self.stats['failed'] += 1
                    if len(self.errors) < 20:
                        self.errors.append(f"Row {i+1} ({row.get('no_nota','?')}): {'; '.join(issues)}")
            else:
                valid.append(row)

        self.stats['parsed'] = len(valid)
        print(f"Valid: {len(valid)}, Failed: {self.stats['failed']}")
        if self.errors:
            print("Errors (first 5):")
            for e in self.errors[:5]: print(f"  {e}")

        if dry_run:
            print("\n[DRY RUN] Preview first 3:")
            for r in valid[:3]:
                print(f"  {r['no_nota']} | {r['tgl_terima']} | {r['customer_name']} | {r['nama_layanan']} | {r['total_tagihan']}")
            return self.stats

        cur = self.pg.cursor()
        for row in valid:
            try:
                self._upsert(cur, row, normalize_addr)
            except Exception as e:
                self.stats['failed'] += 1
                self.errors.append(f"DB: {row.get('no_nota')}: {e}")
                self.pg.rollback()

        # Log
        cur.execute("""INSERT INTO import_logs (filename, imported_by, total_rows, success_rows, failed_rows, duplicate_rows, quality_score)
            VALUES (%s,'script',%s,%s,%s,%s,%s)""",
            (os.path.basename(filepath), self.stats['total'],
             self.stats['inserted']+self.stats['updated'], self.stats['failed'],
             self.stats['duplicate'], self._quality()))
        self.pg.commit()
        self._summary()
        return self.stats

    def _parse_xlsx(self, filepath):
        import openpyxl
        wb = openpyxl.load_workbook(filepath, read_only=False)
        ws = wb.active

        # Unmerge all cells
        for mc in list(ws.merged_cells.ranges):
            ws.unmerge_cells(str(mc))

        # Find header row
        header_row = None
        for rn in range(1, 40):
            vals = [str(c.value or '') for c in ws[rn]]
            if any('No Nota' in v for v in vals):
                header_row = rn; break
        if not header_row:
            raise ValueError("Header not found (looking for 'No Nota')")
        print(f"Header at row {header_row}")

        # Map header names to column indices
        hdr = {}
        for i, cell in enumerate(ws[header_row]):
            if cell.value: hdr[str(cell.value).strip()] = i

        # Column mapping
        COL = {
            'No Nota':'no_nota', 'Customer':'customer_name', 'No Telp Customer':'customer_phone',
            'Alamat Customer':'customer_address', 'Progres Pengerjaan':'progress',
            'Outlet':'outlet', 'Tgl Terima':'tgl_terima_raw', 'Tgl Selesai':'tgl_selesai_raw',
            'Subtotal':'subtotal', 'Tambahan Express':'tambahan_express', 'Diskon':'diskon',
            'Pajak':'pajak', 'Biaya Service':'biaya_service', 'Total Tagihan':'total_tagihan',
            'Jenis':'jenis', 'Pembayaran':'pembayaran', 'Pengambilan':'pengambilan',
            'Tgl Pengambilan':'tgl_pengambilan_raw', 'Pembuat Nota':'kasir', 'Keterangan Nota':'catatan'
        }
        cidx = {}
        for hname, key in COL.items():
            if hname in hdr: cidx[key] = hdr[hname]

        # Detail columns start after col 21 (0-indexed: 21=col22)
        # Col 22: Tipe Item, 23: Nama Layanan, 24: Qty, 25: Satuan, 26: Harga, 27: Jumlah, 28: Keterangan

        rows = []
        for rn in range(header_row + 1, ws.max_row + 1):
            cells = [c.value for c in ws[rn]]
            if not any(c for c in cells): continue

            # Get no_nota
            no_nota = cells[cidx['no_nota']] if 'no_nota' in cidx and cidx['no_nota'] < len(cells) else None
            if not no_nota or not str(no_nota).startswith('SJ'):
                continue  # Skip non-transaction rows (sub-items, totals, etc.)

            row = {}
            for key, idx in cidx.items():
                row[key] = cells[idx] if idx < len(cells) else None

            # Detail columns (first item - Smartlink puts first item on same row)
            row['tipe_item'] = cells[21] if len(cells) > 21 else None
            row['nama_layanan'] = cells[22] if len(cells) > 22 else None
            row['qty'] = safe_float(cells[23] if len(cells) > 23 else None)
            row['satuan'] = cells[24] if len(cells) > 24 else None
            row['harga'] = safe_float(cells[25] if len(cells) > 25 else None)
            row['jumlah_item'] = safe_float(cells[26] if len(cells) > 26 else None)
            row['keterangan'] = cells[27] if len(cells) > 27 else None

            # Parse dates
            row['tgl_terima'] = parse_indo_date(row.get('tgl_terima_raw'))
            row['tgl_selesai'] = parse_indo_date(row.get('tgl_selesai_raw'))
            row['tgl_pengambilan'] = parse_indo_date(row.get('tgl_pengambilan_raw'))

            # Normalize amounts
            for f in ['subtotal','tambahan_express','diskon','pajak','biaya_service','total_tagihan']:
                row[f] = safe_float(row.get(f))

            # Phone
            row['customer_phone'] = normalize_phone(row.get('customer_phone'))

            rows.append(row)

        wb.close()
        return rows

    def _validate(self, row):
        issues = []
        if not row.get('no_nota'): issues.append("Missing no_nota")
        if not row.get('tgl_terima'): issues.append(f"Bad date: {row.get('tgl_terima_raw')}")
        # Multi-item transactions: sub-items may have total=0 but nama_layanan set
        # These are valid - they share the parent's total. Skip amount check for sub-items.
        if row.get('total_tagihan', 0) <= 0 and row.get('subtotal', 0) <= 0:
            # Only fail if this is clearly a bad row (no product info either)
            if not row.get('nama_layanan'):
                issues.append("Zero amount and no product")
            else:
                self.stats['skipped'] += 1
                issues.append("SKIP_SUBITEM")  # Will be filtered, not counted as error
        return issues

    def _upsert(self, cur, row, normalize_addr):
        no_nota = str(row['no_nota']).strip()
        
        # Check duplicate
        cur.execute("SELECT id FROM transactions WHERE no_nota = %s", (no_nota,))
        existing = cur.fetchone()
        
        # Classify product
        nama = row.get('nama_layanan')
        cls = classify_product(nama) if nama else {}
        
        # Normalize address
        addr_norm = None
        addr_conf = None
        if normalize_addr and self.normalizer and row.get('customer_address'):
            nr = self.normalizer.normalize(row['customer_address'])
            addr_norm = nr['normalized']
            addr_conf = nr['confidence']

        # Match customer
        cust_id = None
        if row.get('customer_phone'):
            cur.execute("SELECT id FROM customers WHERE nomor_telpon = %s", (row['customer_phone'],))
            r = cur.fetchone()
            if r: cust_id = r[0]
        if not cust_id and row.get('customer_name'):
            cur.execute("SELECT id FROM customers WHERE LOWER(nama) = %s", (row['customer_name'].strip().lower(),))
            r = cur.fetchone()
            if r: cust_id = r[0]

        if existing:
            # Update existing
            cur.execute("""UPDATE transactions SET
                customer_id=%s, customer_name=%s, customer_phone=%s, customer_address=%s,
                progress_status=%s, tgl_selesai=%s, tgl_pengambilan=%s,
                subtotal=%s, tambahan_express=%s, diskon=%s, pajak=%s, biaya_service=%s, total_tagihan=%s,
                pembayaran=%s, pengambilan=%s, kasir=%s,
                customer_address_normalized=%s, address_confidence=%s
                WHERE no_nota=%s""",
                (cust_id, row.get('customer_name'), row.get('customer_phone'), row.get('customer_address'),
                 row.get('progress'), row.get('tgl_selesai'), row.get('tgl_pengambilan'),
                 row['subtotal'], row['tambahan_express'], row['diskon'], row['pajak'],
                 row.get('biaya_service',0), row['total_tagihan'],
                 row.get('pembayaran'), row.get('pengambilan'), row.get('kasir'),
                 addr_norm, addr_conf, no_nota))
            self.stats['updated'] += 1
        else:
            # Get product mapping id
            pm_id = None
            if nama:
                cur.execute("SELECT id FROM product_mappings WHERE smartlink_name = %s", (nama,))
                r = cur.fetchone()
                if r: pm_id = r[0]

            cur.execute("""INSERT INTO transactions 
                (no_nota, customer_id, customer_name, customer_phone, customer_address,
                 progress_status, outlet, tgl_terima, tgl_selesai, tgl_pengambilan,
                 subtotal, tambahan_express, diskon, pajak, biaya_service, total_tagihan,
                 jenis, pembayaran, pengambilan, kasir, catatan,
                 nama_layanan_original, product_mapping_id, kategori, sub_kategori,
                 speed_tier, is_promo, qty, satuan, harga_item, jumlah_item, keterangan,
                 import_source, customer_address_normalized, address_confidence)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (no_nota, cust_id, row.get('customer_name'), row.get('customer_phone'),
                 row.get('customer_address'), row.get('progress'), row.get('outlet','SIJI Bintaro'),
                 row['tgl_terima'], row.get('tgl_selesai'), row.get('tgl_pengambilan'),
                 row['subtotal'], row['tambahan_express'], row['diskon'], row['pajak'],
                 row.get('biaya_service',0), row['total_tagihan'],
                 row.get('jenis','Reguler'), row.get('pembayaran'), row.get('pengambilan'),
                 row.get('kasir'), row.get('catatan'),
                 nama, pm_id,
                 cls.get('kategori'), cls.get('sub_kategori'),
                 cls.get('speed_tier','REGULER'), cls.get('is_promo', False),
                 row.get('qty'), row.get('satuan'), row.get('harga'), row.get('jumlah_item'),
                 row.get('keterangan'), 'smartlink_import',
                 addr_norm, addr_conf))
            self.stats['inserted'] += 1

    def _quality(self):
        total = self.stats['total'] or 1
        return round((self.stats['inserted'] + self.stats['updated']) / total * 100, 1)

    def _summary(self):
        print(f"\n{'='*50}")
        print(f"Import Summary")
        print(f"{'='*50}")
        print(f"  Total rows:    {self.stats['total']}")
        print(f"  Parsed:        {self.stats['parsed']}")
        print(f"  Inserted:      {self.stats['inserted']}")
        print(f"  Updated:       {self.stats['updated']}")
        print(f"  Failed:        {self.stats['failed']}")
        print(f"  Quality:       {self._quality()}%")
        print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description='SIJI Smartlink Importer')
    parser.add_argument('action', choices=['import', 'validate'])
    parser.add_argument('--file', '-f', required=True, help='Path to XLSX/CSV file')
    parser.add_argument('--dry-run', action='store_true', help='Preview without importing')
    parser.add_argument('--no-normalize', action='store_true', help='Skip address normalization')
    parser.add_argument('--pg-host', default='localhost')
    parser.add_argument('--pg-port', type=int, default=5432)
    parser.add_argument('--pg-db', default='siji_db')
    parser.add_argument('--pg-user', default='siji')
    parser.add_argument('--pg-pass', default='siji2026db')
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"File not found: {args.file}"); sys.exit(1)

    import psycopg2
    pg = psycopg2.connect(host=args.pg_host, port=args.pg_port, dbname=args.pg_db, 
                          user=args.pg_user, password=args.pg_pass)
    
    # Init normalizer
    ref_path = os.path.join(script_dir, 'location_references.json')
    if not os.path.exists(ref_path):
        ref_path = os.path.join(script_dir, '..', 'data', 'location_references.json')
    normalizer = LocationNormalizer(reference_path=ref_path, pg_conn=pg) if not args.no_normalize else None

    importer = SmartlinkImporter(pg, normalizer)

    if args.action == 'validate':
        importer.import_file(args.file, dry_run=True)
    else:
        importer.import_file(args.file, dry_run=args.dry_run, normalize_addr=not args.no_normalize)

    pg.close()

if __name__ == '__main__':
    main()
