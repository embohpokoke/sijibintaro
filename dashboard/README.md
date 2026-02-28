# SIJI Bintaro — Platform Dokumentasi

**Version:** 2.5
**Last Updated:** 2026-02-28
**Manager:** Erik Mahendra

## Project Overview

SIJI Bintaro adalah platform digital terintegrasi untuk bisnis laundry SIJI Bintaro. Terdiri dari website publik, analytics dashboard, CRM WhatsApp, order tracking, monitoring, dan API backend — semua di-host di satu VPS dengan arsitektur microservice.

### Key Stats
| Metric | Value |
|--------|-------|
| Total Transactions | 13,180 (tagihan > 0) |
| Unique Customers | 2,232 phones |
| HVC Customers (5+ orders) | 571 |
| WA Conversations | 600 (518 with names, 468 linked to DB) |
| WA Messages | 16,500+ |
| WA Contacts (GOWA) | 1,141 (1,006 with saved names) |
| Areas Covered | 32 normalized areas |
| Top Customer | Bu Hariza (234 orders, Rp 44.8jt) |
| Data Range | 2021-02-01 to 2026-02-28 |

---

## Architecture

```
                         Internet
                            |
                      [nginx :443]
           ┌─────────┬─────┴──────┬───────────┬────────────┐
           |         |            |           |            |
   sijibintaro.id  dashboard.  crm.      order.        ops.
   (main site)     sijibintaro sijibintaro sijibintaro  sijibintaro
        |              |          |          |            |
   Static HTML    [Dashboard]  [CRM →WA]  [Order     [Ops Monitor]
   + karir/admin  index.html   auto-nav   Tracking]   health/docs
        |              |          |          |            |
        └──────────────┴──────┬───┴──────────┴────────────┘
                              |
                     [uvicorn :8002]
                      FastAPI Backend
                    /api/dashboard/*  ─── Dashboard endpoints
                    /api/dashboard/wa/* ── WA CRM endpoints
                    /api/order/*  ──────── Order tracking (public)
                    /api/admin/*  ──────── Admin/HR endpoints
                    /api/wa/*  ────────── WA webhook + legacy
                    /api/feedback  ────── Customer feedback
                    /api/lamaran  ─────── Job applications
                              |
                    siji_database.db (transactions + WA)
                    siji.db (legacy WA + app data)
                              |
              ┌───────────────┼──────────────┐
              |               |              |
        [GOWA :3002]    [Ollama :11434]  [Cron Jobs]
        WhatsApp API    minimax-m2.5     wa_sync (15m)
        gowa.sijibintaro.id              health (1h)
              |                          backup (daily)
        webhook → /api/wa/gowa-webhook
```

### Tech Stack
- **Frontend:** Single-file HTML/CSS/JS, Chart.js 4.4.0
- **Backend:** Python FastAPI, SQLite3, Pydantic
- **WhatsApp:** GOWA (go-whatsapp-web-multidevice) at `127.0.0.1:3002`
- **LLM:** Ollama (minimax-m2.5:cloud) at `127.0.0.1:11434`
- **Web Server:** nginx (reverse proxy + basic auth + IP whitelist + SSL)
- **Process Manager:** systemd (`sijibintaro-api.service`, `gowa.service`)
- **Host:** Hostinger VPS (`srv1389108.hstgr.cloud`, IP: `72.60.78.181`)
- **SSL:** Let's Encrypt (auto-renew via certbot)

---

## Sitemap

### Subdomains

| Subdomain | Purpose | Auth | SSL | Nginx Config |
|-----------|---------|------|-----|--------------|
| `sijibintaro.id` | Main website + legacy paths | Partial | Yes | `sijibintaro-id.conf` |
| `www.sijibintaro.id` | Redirect → sijibintaro.id | — | Yes | `sijibintaro-id.conf` |
| `dashboard.sijibintaro.id` | Analytics dashboard | Basic auth | Yes | `dashboard-sijibintaro.conf` |
| `crm.sijibintaro.id` | WA CRM (auto-nav WhatsApp tab) | Basic auth | Yes | `crm-sijibintaro.conf` |
| `order.sijibintaro.id` | Public order tracking | **None** | Yes | `order-sijibintaro.conf` |
| `ops.sijibintaro.id` | Operations monitoring | Basic auth | Yes | `ops-sijibintaro.conf` |
| `gowa.sijibintaro.id` | GOWA WhatsApp API | Basic auth + IP whitelist | Yes | `gowa-sijibintaro.conf` |

### URL Map — sijibintaro.id (Main)

| Path | Description | Auth |
|------|-------------|------|
| `/` | Landing page (laundry website) | Public |
| `/karir.html` | Halaman karir/lowongan | Public |
| `/feedback.html` | Form feedback customer | Public |
| `/terima-kasih.html` | Thank you page | Public |
| `/admin.html` | Admin panel (HR, feedback, lamaran) | Public* |
| `/dashboard/` | Analytics dashboard | Basic auth |
| `/wa-dashboard/` | Legacy WA dashboard | Basic auth |
| `/gowa/` | GOWA UI (embedded) | Public |
| `/api/*` | API endpoints | Varies |

### URL Map — dashboard.sijibintaro.id

| Path | Description |
|------|-------------|
| `/` | Dashboard (default: Dashboard page) |
| `/api/dashboard/*` | Dashboard API proxy |
| `/api/dashboard/wa/*` | WA CRM API proxy |

### URL Map — crm.sijibintaro.id

| Path | Description |
|------|-------------|
| `/` | Dashboard (auto-navigates to WhatsApp tab) |
| `/api/dashboard/wa/*` | WA CRM API proxy |

### URL Map — order.sijibintaro.id (Public)

| Path | Description |
|------|-------------|
| `/` | Order tracking page (search by nota/phone/nama) |
| `/?q=SJRG260215` | Direct search via URL parameter |
| `/api/order/track?q=` | Order tracking API |

### URL Map — ops.sijibintaro.id

| Path | Description |
|------|-------------|
| `/` | Ops monitoring dashboard (auto-refresh 60s) |
| `/docs` | FastAPI Swagger UI |
| `/openapi.json` | OpenAPI spec |
| `/api/*` | Full API proxy |

### URL Map — gowa.sijibintaro.id

| Path | Description |
|------|-------------|
| `/` | GOWA Web UI |
| `/chats` | Chat list API |
| `/send/message` | Send message API |
| `/devices` | Device status |

### Dashboard Pages (Single-Page App)

| Page | Tab | Description |
|------|-----|-------------|
| Dashboard | — | Revenue, orders, SLA alerts, trends, ongoing orders |
| Analytics | — | Daily revenue, service breakdown, payment status, monthly trends |
| Orders | — | Filterable order table, click-to-detail modal |
| Customers | Overview | Segments, frequency, top customers, churn risk |
| Customers | Search | Name/phone/address search + area filter |
| Customers | Area Analysis | Area cards, revenue/growth, LLM insight |
| WhatsApp | Conversations | Split-view: conversation list + chat thread |
| WhatsApp | Analytics | Volume, peak hours, response time, topics, active/silent |
| WhatsApp | AI Insight | LLM-powered CRM insights |
| Settings | — | Version info, docs links, quick links |

---

## File Inventory

### Frontend

| File | Path | Description |
|------|------|-------------|
| Dashboard | `/var/www/sijibintaro/dashboard/index.html` | Single-file dashboard (2,332 lines, 6 pages) |
| Order Tracking | `/var/www/sijibintaro/order/index.html` | Public order tracking page |
| Ops Monitor | `/var/www/sijibintaro/ops/index.html` | Operations monitoring page |
| Main Website | `/var/www/sijibintaro/index.html` | Laundry landing page |
| Admin Panel | `/var/www/sijibintaro/admin.html` | HR/feedback admin |
| Feedback Form | `/var/www/sijibintaro/feedback.html` | Customer feedback |
| Karir | `/var/www/sijibintaro/karir.html` | Job listings |
| Backup v2.2 | `/var/www/sijibintaro/dashboard/index.html.bak.20260228` | Dashboard backup |
| Backup v2.1 | `/var/www/sijibintaro/dashboard/index.html.bak.20260222` | Dashboard backup |

### Backend

| File | Path | Description |
|------|------|-------------|
| `main.py` | `/root/sijibintaro.id/api/` | FastAPI app, mounts all routers |
| `dashboard_api.py` | `/root/sijibintaro.id/api/` | Dashboard router (23 endpoints) |
| `wa_crm_api.py` | `/root/sijibintaro.id/api/` | WA CRM router (7 endpoints) |
| `order_tracking_api.py` | `/root/sijibintaro.id/api/` | Public order tracking (1 endpoint) |
| `wa_webhook.py` | `/root/sijibintaro.id/api/` | WhatsApp webhook (Fonnte + GOWA) |
| `wa_sync.py` | `/root/sijibintaro.id/api/` | GOWA → SQLite sync pipeline (conversations, messages, contacts, customer linking) |
| `wa_sync_cron.sh` | `/root/sijibintaro.id/api/` | Cron wrapper for wa_sync |
| `smartlink_importer.py` | `/root/sijibintaro.id/api/` | Smartlink data importer |
| `address_normalizer.py` | `/root/sijibintaro.id/api/` | Address normalization logic |
| `database.py` | `/root/sijibintaro.id/api/` | SQLite connection management |
| `models.py` | `/root/sijibintaro.id/api/` | Pydantic models |

### Infrastructure

| File | Path | Description |
|------|------|-------------|
| nginx main | `/etc/nginx/conf.d/sijibintaro-id.conf` | Main site config |
| nginx dashboard | `/etc/nginx/conf.d/dashboard-sijibintaro.conf` | Dashboard subdomain |
| nginx CRM | `/etc/nginx/conf.d/crm-sijibintaro.conf` | CRM subdomain |
| nginx order | `/etc/nginx/conf.d/order-sijibintaro.conf` | Order tracking subdomain |
| nginx ops | `/etc/nginx/conf.d/ops-sijibintaro.conf` | Ops subdomain |
| nginx GOWA | `/etc/nginx/conf.d/gowa-sijibintaro.conf` | GOWA subdomain (IP whitelist) |
| htpasswd | `/etc/nginx/.wa-dashboard-htpasswd` | Basic auth (users: siji-admin, ocha, filean, siji) |
| systemd API | `/etc/systemd/system/sijibintaro-api.service` | FastAPI service |
| systemd GOWA | `/etc/systemd/system/gowa.service` | GOWA service |
| Database | `/opt/siji-dashboard/siji_database.db` | SQLite (tx + WA tables) |
| GOWA binary | `/opt/gowa/whatsapp` | go-whatsapp-web-multidevice |
| GOWA config | `/opt/gowa/.env` | GOWA environment |
| Health check | `/root/siji-health-check.sh` | Hourly monitoring (cron) |
| Backup | `/root/siji-backup.sh` | Daily DB backup (cron) |

---

## API Reference

### Dashboard Endpoints (prefix: `/api/dashboard`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check + transaction count |
| GET | `/overview` | Revenue, orders, customers, SLA summary + alerts |
| GET | `/revenue/monthly` | Monthly revenue/orders (last 72 months) |
| GET | `/revenue/daily` | Daily revenue for selected period |
| GET | `/revenue/by-service` | Revenue by category and service |
| GET | `/orders` | Paginated order list (filter by status, search) |
| GET | `/orders/ongoing` | Active orders (not 100% or not picked up) |
| GET | `/products` | Product/service breakdown |
| GET | `/payment-status` | Payment distribution |
| GET | `/locations` | Top 20 locations by orders |
| GET | `/customers/summary` | Total, active, HVC, churn stats + segments |
| GET | `/customers/top` | Top customers by lifetime value |
| GET | `/customers/hvc` | High-value customers (5+ orders or 1M+ spent) |
| GET | `/customers/churn-risk` | Customers at churn risk (30+ days inactive) |
| GET | `/customers/frequency` | Order frequency distribution |
| GET | `/customers/search?q=&area=` | Search by name/phone/address |
| GET | `/customers/detail?phone=` | Full customer profile |
| GET | `/areas/list` | All areas with order counts |
| GET | `/areas/analysis` | Per-area analytics with growth |
| POST | `/analysis/llm` | AI insight (body: `{context_type, data}`) |

### WA CRM Endpoints (prefix: `/api/dashboard/wa`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/conversations` | Paginated conversations (filter: all/customer/unknown) |
| GET | `/messages?phone=` | Chat history for a phone number |
| GET | `/stats` | Total conversations, messages, today count |
| GET | `/search?q=` | Full-text message search |
| GET | `/export?phone=` | CSV export of chat history |
| GET | `/analytics` | Response time, volume, peak hours, topics |
| GET | `/insights` | LLM-powered CRM insights |

### Order Tracking (prefix: `/api/order`) — Public

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/track?q=` | Search by nota number, phone, or customer name |

### Webhook

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/wa/gowa-webhook` | GOWA webhook (message, ack, revoke, edit) |
| POST | `/api/wa/webhook/{token}` | Fonnte webhook (legacy) |

### Admin/HR Endpoints (prefix: `/api/admin`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/lamaran` | Job applications CRUD |
| GET/POST | `/karyawan` | Employee management |
| GET | `/feedback` | Customer feedback list |
| GET/POST | `/presensi` | Attendance tracking |
| POST | `/lamaran/{id}/send-wa` | Send WA to applicant |

### Public Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/feedback` | Submit customer feedback |
| POST | `/api/lamaran` | Submit job application |
| GET | `/api/reviews` | Public reviews |

---

## Features

### Dashboard Pages

1. **Dashboard** — Overview cards (revenue, order status, SLA alerts, top products, customers, target), sales trend chart, order status donut, top services list, ongoing orders scroll
2. **Analytics** — Daily revenue bar chart, service category donut, payment status donut, monthly trend line chart
3. **Orders** — Filterable/searchable order table with pagination, click-to-detail modal
4. **Customers** — Three tabs:
   - *Overview* — Segment/frequency charts, top customers table, churn risk table
   - *Search* — Name/phone/address search with area dropdown filter
   - *Area Analysis* — Area cards grid with revenue/growth, click for detail + LLM insight
5. **WhatsApp** — Three tabs:
   - *Conversations* — Split-view: conversation list + chat thread, filter, search, export
   - *Analytics* — Volume, peak hours, response time, topics, active/silent customers
   - *AI Insight* — LLM-powered CRM insights via Ollama
6. **Settings** — Dashboard metadata, documentation links, quick links

### Order Tracking (Public)
- Search by nota number, phone number, or customer name
- Shows: service, amount, payment status, progress, pickup status
- Visual progress bar per order
- Supports URL params: `order.sijibintaro.id/?q=SJRG260215`
- Mobile responsive, dark theme matching brand

### Ops Monitoring
- Service health checks (FastAPI, WA Pipeline, GOWA, Dashboard API)
- Database stats (transactions, WA conversations, messages)
- Recent WA conversations overview
- Quick links to all subdomains and API docs
- Auto-refresh every 60 seconds

### Date Picker (Shared Component)
Three modes on Dashboard, Analytics, Orders pages:
- **Bulanan** — Monthly selector (72 months back)
- **Tahunan** — Year selector (2021–current)
- **Custom** — Date range (from/to inputs)

### SLA Monitoring
| Service Keyword | SLA (days) |
|----------------|-----------|
| Express / 24JAM | 1 |
| Reguler / Setrika / Laundry | 3 |
| Sepatu / Shoes | 5 |
| Tas / Bag | 7 |
| Karpet / Gordyn / Kasur | 8 |

### LLM Integration
- Model: `minimax-m2.5:cloud` via Ollama at `127.0.0.1:11434`
- Response time: ~6.5 seconds
- Context types: `area`, `customer`, `wa_crm`
- Graceful fallback if Ollama unavailable

---

## Setup & Deployment

### Prerequisites
- Python 3.8+ with pip
- nginx with certbot
- Ollama (optional, for LLM insights)
- GOWA binary (for WhatsApp integration)

### Start Services
```bash
systemctl start sijibintaro-api   # FastAPI on :8002
systemctl start gowa              # GOWA on :3002
systemctl start nginx             # Reverse proxy
```

### Verify
```bash
# Health check
curl -s http://127.0.0.1:8002/api/dashboard/health

# Order tracking (public)
curl -s "http://127.0.0.1:8002/api/order/track?q=hariza"

# WA stats
curl -s http://127.0.0.1:8002/api/dashboard/wa/stats

# All subdomains
for d in dashboard crm order ops gowa; do
  echo "$d: $(curl -sk -o /dev/null -w '%{http_code}' https://$d.sijibintaro.id/)"
done
```

### Restart After Changes
```bash
systemctl restart sijibintaro-api
systemctl reload nginx
```

---

## Rollback

### Frontend
```bash
# Rollback to v2.2
cp /var/www/sijibintaro/dashboard/index.html.bak.20260228 /var/www/sijibintaro/dashboard/index.html

# Rollback to v2.1
cp /var/www/sijibintaro/dashboard/index.html.bak.20260222 /var/www/sijibintaro/dashboard/index.html
```

### Backend
Restore previous Python files, then:
```bash
systemctl restart sijibintaro-api
```

---

## Roadmap

### Phase 1 — Foundation (DONE)
- [x] v2.0: Rewrite from Streamlit to HTML/JS + FastAPI (2026-02-22)
- [x] v2.1: Dark theme, date picker, sidebar navigation (2026-02-22)
- [x] v2.2: SLA monitoring, customer search/detail, area analysis, LLM insights (2026-02-22)

### Phase 2 — WhatsApp Integration (DONE)
- [x] v2.3: GOWA deployment, WA data pipeline, webhook handler (2026-02-28)
- [x] v2.3: WA CRM dashboard tab, analytics, AI insights (2026-02-28)

### Phase 3 — Multi-Subdomain Platform & Contact Sync (DONE)
- [x] v2.4: Subdomain architecture (dashboard/crm/order/ops) (2026-02-28)
- [x] v2.4: Public order tracking page (order.sijibintaro.id) (2026-02-28)
- [x] v2.4: Ops monitoring page with service health checks (2026-02-28)
- [x] v2.4: SSL certificates for all subdomains (2026-02-28)
- [x] v2.5: WA contact name sync from GOWA phone contacts (2026-02-28)
- [x] v2.5: Multi-user authentication (4 users) (2026-02-28)
- [x] v2.5: GOWA basic auth + IP whitelist (satisfy any) (2026-02-28)

### Phase 4 — Payment Integration (PLANNED)
- [ ] Research & select payment gateway provider (see Payment Integration docs)
- [ ] Implement payment API endpoint (`/api/payment/create`, `/api/payment/callback`)
- [ ] Payment link generation (kirim via WhatsApp ke customer)
- [ ] QRIS code generation for in-store payment
- [ ] Payment status tracking in dashboard
- [ ] Auto-reconciliation: match payment with order nota
- [ ] Payment analytics in dashboard (daily/monthly collection rate)

### Phase 5 — Automation & Growth (PLANNED)
- [ ] Auto WA reminder untuk order siap diambil
- [ ] Auto WA follow-up untuk customer churn risk
- [ ] Customer loyalty program (points/rewards)
- [ ] Multi-outlet support
- [ ] Employee performance dashboard
- [ ] Expense tracking & profit calculation
- [ ] Mobile app (PWA or native)

---

## Payment Integration Plan

> Dokumen lengkap: lihat [PAYMENT-INTEGRATION.md](PAYMENT-INTEGRATION.md)

### Why Payment Integration?
SIJI Bintaro saat ini menerima pembayaran manual (cash/transfer). Dengan payment gateway:
1. **Customer convenience** — Bayar via QRIS, e-wallet, VA dari HP
2. **Auto-reconciliation** — Status pembayaran otomatis update di sistem
3. **Payment link via WA** — Kirim link bayar langsung ke customer dari CRM
4. **Reduce human error** — Tidak perlu manual verifikasi transfer
5. **Analytics** — Track collection rate, payment method preferences

### Integration Approach
```
Customer                    SIJI System                  Payment Gateway
   |                            |                              |
   |  1. Order selesai          |                              |
   |  ←── WA: Payment Link ────|                              |
   |                            |  2. Create transaction ───→  |
   |  3. Bayar (QRIS/VA/ewallet)                              |
   |  ─────────────────────────────────────────────────────→   |
   |                            |  4. Webhook callback  ←────  |
   |                            |  5. Update payment status    |
   |  ←── WA: Konfirmasi ──────|                              |
```

### Recommended Flow
1. Order selesai di Smartlink POS → Masuk database via importer
2. Backend generate payment link via gateway API
3. Payment link dikirim via GOWA WhatsApp ke customer
4. Customer bayar → Gateway kirim webhook callback
5. Backend update `pembayaran` status di database
6. Dashboard real-time menampilkan status pembayaran

---

## Changelog

### v2.5 — Contact Sync & Multi-User Auth (2026-02-28)
- **WA contact name sync:** `wa_sync.py` now fetches saved contact names from GOWA `/user/my/contacts` endpoint (1,006 contacts with names)
- **Name resolution priority:** customer_name (from transaction DB) > contact_name (from phone contacts) > pushname (from WhatsApp) > phone number (fallback)
- **WA name coverage:** 518/599 conversations now have names (was 490 before contact sync)
- **Multi-user auth:** 4 users (siji-admin, ocha, filean, siji) across all authenticated subdomains
- **GOWA auth update:** Added basic auth alongside IP whitelist (`satisfy any` in nginx) — access from any IP with credentials
- **Sync pipeline:** 4-step sync: conversations → messages → contact names → customer linking

### v2.4 — Multi-Subdomain Platform (2026-02-28)
- **Subdomains:** 4 new subdomains (dashboard, crm, order, ops) with SSL
- **Order tracking:** Public page at `order.sijibintaro.id` — search by nota, phone, or name
- **Order tracking API:** `GET /api/order/track?q=` — public endpoint, no auth
- **Ops monitoring:** `ops.sijibintaro.id` — service health, DB stats, recent WA, auto-refresh
- **CRM subdomain:** `crm.sijibintaro.id` — auto-navigates to WhatsApp tab
- **Dashboard subdomain:** `dashboard.sijibintaro.id` — clean URL for analytics
- **Nginx:** 4 new configs with HTTPS redirect + Let's Encrypt SSL

### v2.3 — GOWA WhatsApp Integration (2026-02-28)
- **GOWA deployed:** go-whatsapp-web-multidevice binary at `/opt/gowa/`, port 3002
- **WA data pipeline:** `wa_sync.py` syncs 600 conversations + 8,253 messages
- **New DB tables:** `wa_conversations`, `wa_messages` in `siji_database.db`
- **Webhook handler:** Real-time capture via `/api/wa/gowa-webhook`
- **Dual-write:** Webhook writes to both `siji.db` and `siji_database.db`
- **Customer linking:** 468 WA conversations auto-linked to transaction customers
- **WA CRM API:** 7 endpoints under `/api/dashboard/wa/`
- **WhatsApp dashboard tab:** Split-view conversations + chat, stats, search, filter, export
- **WA Analytics:** Volume, peak hours, response time, topics, active/silent customers
- **WA AI Insight:** LLM-powered CRM analysis via Ollama
- **Health monitoring:** Hourly cron, alerts via WhatsApp
- **Daily backup:** Gzipped DB backup, 7-day retention

### v2.2 (2026-02-22)
- MTD period label, SLA alerts, order detail modal
- Customer search/detail/area tabs with LLM insights
- 6 new backend endpoints
- Modal system with Escape/click-outside close

### v2.1 (2026-02-22)
- Date picker (Bulanan/Tahunan/Custom)
- Dark theme, sidebar + mobile bottom nav

### v2.0 (2026-02-22)
- Full rewrite from Streamlit to HTML/JS + FastAPI
- Migrated from PostgreSQL to SQLite

### v1.0 (Legacy)
- Streamlit dashboard at port 8501
- PostgreSQL backend (deprecated)
