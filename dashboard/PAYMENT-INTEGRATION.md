# SIJI Bintaro — Payment Integration Plan

**Version:** 1.0
**Date:** 2026-02-28
**Status:** Research & Planning

---

## Latar Belakang

SIJI Bintaro saat ini menerima pembayaran manual (cash / transfer bank). Dengan integrasi payment gateway:
1. **Customer convenience** — Bayar via QRIS, e-wallet, VA dari HP
2. **Auto-reconciliation** — Status pembayaran otomatis update di sistem
3. **Payment link via WA** — Kirim link bayar langsung ke customer dari CRM
4. **Reduce human error** — Tidak perlu manual verifikasi transfer
5. **Analytics** — Track collection rate, payment method preferences

---

## Regulasi QRIS (Bank Indonesia)

| Kategori Merchant | Nominal Transaksi | MDR |
|---|---|---|
| **Usaha Mikro (UMI)** | s/d Rp 500.000 | **0% (gratis)** sejak Des 2024 |
| **Usaha Mikro (UMI)** | > Rp 500.000 | **0.3%** |
| **Usaha Kecil/Menengah** | Semua nominal | **0.7%** |

> SIJI Bintaro kemungkinan masuk kategori Usaha Kecil, jadi QRIS MDR = **0.7%**.
> Rata-rata tagihan laundry Rp 50k-200k → fee QRIS Rp 350-1.400 per transaksi.

---

## Perbandingan Payment Gateway

### Tabel Ringkas

| Fitur | Midtrans | Xendit | Tripay | Duitku | iPaymu | OY! |
|---|---|---|---|---|---|---|
| **NPWP Wajib** | Ya | Ya* | Tidak | Ya | Tidak | Tidak |
| **Biaya Bulanan** | Gratis | Gratis | Gratis | Gratis | Gratis | Gratis |
| **QRIS** | 0.7% | 0.63% | Rp750+0.7% | 0.7% | 0.7% | 0.7% |
| **VA (rata-rata)** | Rp 4.000 | Rp 4.000 | Rp 4.250 | Rp 3.000 | ~Rp 3.500 | ~Rp 4.000 |
| **VA (termurah)** | Rp 4.000 | Rp 4.000 | Rp 4.250 | **Rp 1.500** | ~Rp 1.000 | ~Rp 4.000 |
| **Credit Card** | 2.9%+Rp2k | ~2.9%+Rp2k | — | 2.9%+Rp2k | ~2.9% | ~2.9%+ |
| **E-wallet** | 2% | 1.5% | ~0.7%+ | **1.67%** | ~1.5% | ~1.5% |
| **Settlement VA** | D+3 cutoff | Instant* | Varies | Varies | **H+0** | Instant* |
| **Payment Link** | API+UI | API+UI | API | API | Social | API+UI |
| **Sandbox** | Ya | Ya | Ya | Ya | Ya | Ya |
| **Python SDK** | Ya | Ya | Community | Ya | Ya | Ya |
| **WA Payment Link** | Ya | Ya | Ya | Ya | Ya | Ya |

\* Xendit: instant untuk VA kecuali BCA (T+1); NPWP bisa diganti KTP untuk beberapa channel
\* OY!: instant untuk VA kecuali BCA (H+2)

---

## Detail Per Provider

### 1. MIDTRANS (GoTo/Gojek)

**Website:** midtrans.com | **Docs:** docs.midtrans.com

**Registrasi:** KTP + NPWP. Ada "Midtrans GO" untuk usaha tanpa badan hukum.

**Fee:**
| Metode | Biaya |
|---|---|
| QRIS | 0.7% (incl. PPN) |
| GoPay | 2% (incl. PPN) |
| ShopeePay | ~2% (incl. PPN) |
| Virtual Account (semua bank) | Rp 4.000 flat |
| Credit Card | 2.9% + Rp 2.000 |
| Alfamart/Indomaret | ~Rp 5.000 |

**Settlement:** Credit Card D+1, VA/E-wallet D+3 cutoff, minimum Rp 50.000.

**Integrasi:**
- **Snap API** — hosted checkout popup (paling simpel)
- **Core API** — full backend control
- **Payment Link API** — `POST /v1/payment-links` → dapat URL, kirim ke WA
- **SDK:** PHP, Node.js, Go, Python, Ruby, Java
- **Webhook:** notifikasi status pembayaran ke endpoint kamu
- **Invoicing:** buat invoice bermerek dari dashboard

**Keunggulan:** Ekosistem besar (500k+ bisnis), native GoPay, PCI DSS Level 1, Snap API sangat mudah.

---

### 2. XENDIT (Recommended)

**Website:** xendit.co | **Docs:** developers.xendit.co

**Registrasi:** KTP + NPWP (KTP bisa ganti NPWP untuk OVO, Alfamart, ShopeePay, LinkAja).

**Fee:**
| Metode | Biaya |
|---|---|
| QRIS | **0.63%** (termurah) |
| Virtual Account (BNI, BRI, Mandiri, dll) | Rp 4.000 flat |
| OVO | 2.73% |
| ShopeePay | 2% |
| DANA (with PIN) | 1.5% |
| LinkAja | 1.5% |
| Credit Card | ~2.9% + Rp 2.000 |

**Settlement:**
| Metode | Waktu |
|---|---|
| VA (BRI, BNI, Mandiri, Permata) | **Instant / Real-time** |
| VA (BCA) | T+1 |
| E-wallet | T+2 |
| QRIS | T+2 |
| Credit Card | T+5 |

**Integrasi:**
- **REST API** lengkap: Invoice, Payment Link, VA, E-Wallet, QRIS, Disbursement
- **SDK:** PHP, Node.js, Python, Go, Ruby, Java, .NET
- **xenInvoice** — buat & kirim invoice via WhatsApp/social media (tanpa website)
- **Payment Link** — dashboard (no-code) + API
- **Webhook:** komprehensif untuk semua event pembayaran
- **Early Settlement** tersedia (bayar fee kecil untuk pencairan lebih cepat)

**Keunggulan:** API docs terbaik, instant settlement VA, QRIS termurah (0.63%), xenInvoice cocok untuk WA-based CRM.

---

### 3. DUITKU (Termurah)

**Website:** duitku.com | **Docs:** docs.duitku.com

**Registrasi:** KTP + NPWP + nomor rekening bank.

**Fee:**
| Metode | Biaya |
|---|---|
| QRIS | 0.7% |
| VA (Artha Graha, Sahabat Sampoerna) | **Rp 1.500** (termurah!) |
| VA (BRI, BNI, Permata, CIMB) | Rp 3.000 |
| VA (Mandiri) | Rp 4.000 |
| VA (BCA) | Rp 5.000 |
| OVO / DANA / LinkAja | **1.67%** (termurah e-wallet) |
| ShopeePay | 2% |
| Credit Card | 2.9% + Rp 2.000 |
| Alfamart | Rp 2.500 |

**Settlement:** E-wallet/QRIS D+2, VA varies.

**Keunggulan:** Fee paling murah untuk VA dan e-wallet. Cocok untuk maximize profit margin.

---

### 4. iPAYMU (Tanpa NPWP + Settlement H+0)

**Website:** ipaymu.com | **Docs:** ipaymu.com/en/api-documentation/

**Registrasi:** KTP + selfie KTP saja. **NPWP tidak wajib.** Verifikasi max 2 hari kerja.

**Fee:**
| Metode | Biaya |
|---|---|
| QRIS | 0.7% |
| Virtual Account | ~Rp 1.000 - Rp 5.000 |
| Payment Link | Rp 1.000 per transaksi sukses |
| E-wallet | Min Rp 10.000, max Rp 5.000.000 |

**Settlement:** **H+0 (hari yang sama)** — paling cepat di market!

**Keunggulan:** Tanpa NPWP, settlement instan, social media payment link untuk WhatsApp.

---

### 5. TRIPAY (KTP Saja)

**Website:** tripay.co.id | **Docs:** tripay.co.id/developer

**Registrasi:** **KTP saja.** Tidak perlu NPWP atau dokumen badan usaha. Paling mudah daftar.

**Fee:**
| Metode | Biaya |
|---|---|
| QRIS | Rp 750 + 0.7% |
| Virtual Account | Rp 4.250 - Rp 5.500 |
| E-wallet | Rp 750 + 0.7% - 3% |
| Alfamart/Indomaret | Rp 3.000 - Rp 3.500 |

**Keunggulan:** Registrasi termudah (KTP only). Populer di kalangan dropshipper/reseller.

---

### 6. OY! INDONESIA (KTP + Instant Settlement)

**Website:** oyindonesia.com | **Docs:** api-docs.oyindonesia.com

**Registrasi:** **KTP saja** (selfie + foto KTP, auto-fill). Tanpa NPWP.

**Fee:** Tidak fully public, estimasi VA ~Rp 4.000, QRIS 0.7%, e-wallet ~1.5-2.5%.

**Settlement:** VA instant (kecuali BCA H+2), e-wallet cut-off 23:59.

**Keunggulan:** KTP-only, real-time settlement, halaman khusus UMKM, PCI DSS v4.0.1.

---

### Supplement: MOOTA (Bank Mutation Tracker)

**Website:** moota.co — **Bukan payment gateway**, tapi monitor mutasi bank.

- Otomatis track transfer masuk ke rekening BCA/Mandiri/BNI/BRI
- Webhook ke sistem kamu setiap ada mutasi baru
- **Rp 45.000/bulan** per rekening bank
- Cocok sebagai supplement: untuk customer yang prefer "transfer langsung"
- Tidak support QRIS, e-wallet, credit card

---

## Rekomendasi untuk SIJI Bintaro

### Pilihan Utama: **Xendit**

| Alasan | Detail |
|---|---|
| API docs terbaik | Developer experience paling bagus, Python SDK |
| QRIS termurah | 0.63% vs 0.7% standard |
| Instant settlement VA | BRI, BNI, Mandiri langsung masuk |
| Payment Link via WA | xenInvoice perfect untuk kirim link via WhatsApp |
| Early Settlement | Bisa cairkan lebih cepat dengan fee kecil |
| Webhook solid | Integrasi mudah dengan FastAPI backend |

### Alternatif: **Midtrans**

| Alasan | Detail |
|---|---|
| Ekosistem terbesar | 500k+ bisnis, sangat stabil |
| Snap API super simpel | Satu API call = full checkout page |
| Native GoPay | Integrasi langsung tanpa 3rd party |
| Payment Link API | Mudah generate link, kirim via WA |

### Budget Option: **Duitku**

Jika prioritas adalah minimize fee per transaksi (VA Rp 1.500, e-wallet 1.67%).

### Quick Start: **Tripay atau iPaymu**

Jika belum punya NPWP dan butuh mulai cepat. iPaymu bonus settlement H+0.

---

## Integration Architecture

```
                    SIJI Bintaro System
                          |
              ┌───────────┼───────────┐
              |           |           |
        [Order Ready]  [Payment]  [Webhook]
              |         Gateway       |
              |           |           |
    1. Generate   2. Create      4. Receive
       payment       payment        callback
       link          via API
              |           |           |
    3. Send link   Payment      5. Update
       via WA      Page/QRIS      pembayaran
       (GOWA)                     di database
```

### Flow Detail

1. **Order siap** → Backend detect order dengan status "Siap Diambil" atau "Belum Lunas"
2. **Create payment** → `POST /api/payment/create` → call gateway API → dapat payment URL/QRIS
3. **Kirim via WA** → Payment link dikirim ke customer via GOWA WhatsApp API
4. **Customer bayar** → Buka link, pilih metode (QRIS/VA/e-wallet), bayar
5. **Webhook callback** → Gateway POST ke `/api/payment/callback` → update database
6. **Konfirmasi** → Auto WA ke customer: "Pembayaran diterima, terima kasih!"

### API Endpoints (Planned)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/payment/create` | Create payment for an order (nota + amount) |
| POST | `/api/payment/callback` | Webhook receiver from payment gateway |
| GET | `/api/payment/status?nota=` | Check payment status for an order |
| GET | `/api/payment/history` | Payment history with filters |
| GET | `/api/payment/analytics` | Payment analytics (collection rate, methods) |

### Database Changes (Planned)

```sql
CREATE TABLE payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    no_nota TEXT NOT NULL,
    customer_phone TEXT,
    amount REAL NOT NULL,
    payment_method TEXT,          -- qris, va_bca, gopay, etc.
    gateway_reference TEXT,       -- payment gateway transaction ID
    payment_url TEXT,             -- payment link URL
    status TEXT DEFAULT 'pending', -- pending, paid, expired, failed
    paid_at TEXT,
    gateway_callback TEXT,        -- raw callback JSON
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (no_nota) REFERENCES transactions(no_nota)
);

CREATE INDEX idx_payments_nota ON payments(no_nota);
CREATE INDEX idx_payments_status ON payments(status);
```

### Estimasi Biaya per Bulan

Berdasarkan data SIJI Bintaro (Feb 2026):
- ~600 transaksi/bulan, rata-rata Rp 100.000/transaksi
- Total revenue ~Rp 40.000.000/bulan

| Metode | Asumsi % Usage | Fee/tx | Total Fee/bulan |
|---|---|---|---|
| QRIS (60%) | 360 tx | Rp 700 (0.7%) | Rp 252.000 |
| VA (20%) | 120 tx | Rp 4.000 | Rp 480.000 |
| E-wallet (15%) | 90 tx | Rp 1.500 (1.5%) | Rp 135.000 |
| Cash (5%) | 30 tx | Rp 0 | Rp 0 |
| **Total** | | | **~Rp 867.000/bulan** |

> Fee ~2.2% dari total revenue. Bisa lebih murah dengan Duitku (VA Rp 1.500) atau Xendit (QRIS 0.63%).

---

## Implementation Checklist

### Phase 1: Setup (1-2 hari)
- [ ] Pilih payment gateway (Xendit recommended)
- [ ] Daftar akun merchant (KTP + NPWP)
- [ ] Aktivasi sandbox/testing environment
- [ ] Generate API keys (sandbox + production)

### Phase 2: Backend (2-3 hari)
- [ ] Install SDK (`pip install xendit-python` atau `midtransclient`)
- [ ] Buat `payment_api.py` router di FastAPI
- [ ] Implement `POST /api/payment/create`
- [ ] Implement webhook receiver `POST /api/payment/callback`
- [ ] Buat `payments` table di SQLite
- [ ] Test end-to-end di sandbox

### Phase 3: Frontend (1-2 hari)
- [ ] Tambah tombol "Kirim Payment Link" di order detail modal
- [ ] Tambah payment status badge di orders table
- [ ] Tambah payment analytics di dashboard
- [ ] Update order tracking page dengan status pembayaran real-time

### Phase 4: WhatsApp Integration (1 hari)
- [ ] Auto-generate payment link saat order ready
- [ ] Kirim payment link via GOWA ke customer
- [ ] Auto WA konfirmasi setelah pembayaran berhasil

### Phase 5: Go Live (1 hari)
- [ ] Switch dari sandbox ke production API keys
- [ ] Test dengan transaksi real (nominal kecil)
- [ ] Monitor webhook reliability
- [ ] Verify settlement masuk ke rekening

---

## Referensi

- [Midtrans Documentation](https://docs.midtrans.com/)
- [Midtrans Payment Link API](https://docs.midtrans.com/docs/payment-link-via-api)
- [Xendit Developer Docs](https://developers.xendit.co/api-reference/)
- [Xendit Payment Links](https://www.xendit.co/en-id/products/payment-links/)
- [Duitku Pricing](https://www.duitku.com/en/pricing/)
- [Duitku API Docs](https://docs.duitku.com/api/en/)
- [iPaymu API Documentation](https://ipaymu.com/en/api-documentation/)
- [OY! API Documentation](https://api-docs.oyindonesia.com/)
- [Tripay Developer Guide](https://tripay.co.id/developer)
- [Moota Technical Docs](https://moota.gitbook.io/technical-docs/mutation)
- [Bank Indonesia QRIS MDR Policy](https://paydia.id/bank-indonesia-bebaskan-mdr-qris-per-1-desember-2024/)
- [kawula.id Payment Gateway Comparison](https://kawula.id/payment-gateway-murah/)
