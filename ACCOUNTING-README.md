# Panduan Sistem Keuangan SIJI

Dokumen ini dipakai agent OpenClaw SIJI saat membantu Ocha atau kasir mencatat keuangan operasional.

## Base URL

`https://sijibintaro.id/api/accounting/`

## Cara Catat Pengeluaran

Contoh input:

- `beli deterjen Rinso 5kg Tokopedia 450rb`
- `bayar listrik 850.000`
- `servis AC Pak Andi 2.500.000 untuk 3 mesin`
- `beli plastik + spunbond Shopee 320rb`

Langkah agent:

1. Parse nominal, deskripsi, supplier, dan kategori.
2. Cari supplier dulu: `GET /api/accounting/supplier/search?q=<nama>`.
3. Kalau supplier belum ada, buat via `POST /api/accounting/supplier`.
4. Catat pengeluaran via `POST /api/accounting/pengeluaran`.
5. Konfirmasi ke user dengan nominal, kategori, dan supplier yang tersimpan.
6. Jika ada foto struk, simpan file lalu isi `foto_struk`.

## Cara Catat Pemasukan Manual

Contoh input:

- `Bu Tati bayar bedcover 3 pcs 210rb QRIS`
- `Bu Fetty transfer 110rb CKS + satuan`

Langkah agent:

1. Parse `nama_customer`, `layanan`, `nominal`, dan `metode_bayar`.
2. Kirim `POST /api/accounting/pemasukan`.
3. Jika nanti transaksi Smartlink sudah muncul, isi `transaction_id` agar tidak double count.

## Cara Request Laporan

- `laporan Maret 2026` → `GET /api/accounting/laporan/summary?bulan=3&tahun=2026`
- `kirim Excel laporan Maret` → `GET /api/accounting/laporan/export?bulan=3&tahun=2026&format=xlsx`
- `kirim PDF laporan Maret` → `GET /api/accounting/laporan/export?bulan=3&tahun=2026&format=pdf`
- `mutasi yang belum tercatat` → `GET /api/accounting/mutasi?status_link=unlinked`

## Endpoint Penting

- `GET /api/accounting/kategori`
- `POST /api/accounting/kategori`
- `GET /api/accounting/supplier`
- `POST /api/accounting/supplier`
- `POST /api/accounting/pengeluaran`
- `GET /api/accounting/pengeluaran`
- `POST /api/accounting/pemasukan`
- `GET /api/accounting/pemasukan`
- `POST /api/accounting/mutasi/import`
- `PATCH /api/accounting/mutasi/{id}/link`

## Daftar Kategori Awal

- `Gaji & Honor` → Gaji Karyawan, Honor Kasir, Bonus
- `Bahan & Supplies` → Deterjen, Plastik, Tas Spunbond, Gas, Air Galon, Supplies Lainnya
- `Utilitas` → Listrik, Air PAM, Internet
- `Perawatan & Perbaikan` → Service Mesin, Vendor Eksternal, Renovasi
- `Belanja Online` → Tokopedia, Shopee, Lazada, E-commerce Lainnya
- `Operasional` → Kurir / Ongkir, Transportasi, ATK
- `Lain-lain`

## Mapping Layanan SIJI

- `CKS / CES` → `Cuci Kering Setrika` (`LAUNDRY_KILOAN`)
- `CKL` → `Cuci Kering Lipat` (`LAUNDRY_KILOAN`)
- `Bedco` → `Bedcover` (`BEDDING`)
- `Sprei` → `Sprei Set` (`BEDDING`)
- `Satuan` → `Laundry Satuan Reguler` (`LAUNDRY_SATUAN`)
- `Satuan Exp` → `Laundry Satuan Express` (`LAUNDRY_SATUAN`)
- `Bag Spa` → `Tas Reguler / USA / EU Brand` (`TAS`)
- `DC` → `Dry Clean` (`DRY_CLEAN`)
- `Sepatu` → `Shoes Care` (`SEPATU`)

## Catatan Operasional

- Semua endpoint mengandalkan JWT SSO existing via cookie `siji_session`.
- Jangan hardcode supplier. Kalau vendor baru muncul, simpan ke tabel `supplier`.
- Pengeluaran dihapus via soft delete, bukan hard delete.
- Pemasukan manual yang sudah terhubung ke `transactions.id` tidak ikut dijumlahkan dua kali di laporan.
