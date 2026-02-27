# SIJI Bintaro Dashboard

**Version:** 2.2
**Last Updated:** 2026-02-22
**Manager:** Erik Mahendra

## Project Overview

SIJI Bintaro Dashboard is a web-based analytics dashboard for SIJI Bintaro laundry business. It provides real-time visibility into revenue, orders, customer behavior, SLA compliance, and area-level performance. The system consists of a single-file HTML frontend (HTML/CSS/JS + Chart.js) and a FastAPI backend serving data from an SQLite database containing 13,180 transactions spanning February 2021 to February 2026.

### Key Stats
| Metric | Value |
|--------|-------|
| Total Transactions | 13,180 (tagihan > 0) |
| Unique Customers | 2,232 phones |
| HVC Customers (5+ orders) | 571 |
| Areas Covered | 32 normalized areas |
| Top Customer | Bu Hariza (234 orders, Rp 44.8jt) |
| Data Range | 2021-02-01 to 2026-02-16 |

## Architecture

```
                  Internet
                     |
               [nginx :443]
               /           \
    /dashboard/          /api/dashboard/
         |                      |
   [index.html]        [uvicorn :8002]
   Static HTML          FastAPI + SQLite
                             |
                    siji_database.db
                             |
                   [Ollama :11434]
                   minimax-m2.5:cloud
```

### Tech Stack
- **Frontend:** Single-file HTML/CSS/JS, Chart.js 4.4.0
- **Backend:** Python FastAPI, SQLite3, Pydantic
- **LLM:** Ollama (minimax-m2.5:cloud) at `127.0.0.1:11434`
- **Web Server:** nginx (reverse proxy + basic auth)
- **Process Manager:** systemd (`sijibintaro-api.service`)
- **Host:** Hostinger VPS (`srv1389108.hstgr.cloud`)
- **Domain:** `sijibintaro.id`

## File Inventory

### Frontend
| File | Path | Description |
|------|------|-------------|
| `index.html` | `/var/www/sijibintaro/dashboard/index.html` | Single-file dashboard (1,760 lines) |
| `index.html.bak.20260222` | `/var/www/sijibintaro/dashboard/` | Backup of v2.1 |

### Backend
| File | Path | Description |
|------|------|-------------|
| `main.py` | `/root/sijibintaro-api/main.py` | FastAPI app, mounts all routers |
| `dashboard_api.py` | `/root/sijibintaro-api/dashboard_api.py` | Dashboard router (998 lines, 23 endpoints) |
| `wa_webhook.py` | `/root/sijibintaro-api/wa_webhook.py` | WhatsApp Fonnte webhook |
| `smartlink_importer.py` | `/root/sijibintaro-api/smartlink_importer.py` | Smartlink data importer |
| `address_normalizer.py` | `/root/sijibintaro-api/address_normalizer.py` | Address normalization logic |
| `database.py` | `/root/sijibintaro-api/database.py` | SQLite connection management |
| `models.py` | `/root/sijibintaro-api/models.py` | Pydantic models |
| `requirements.txt` | `/root/sijibintaro-api/requirements.txt` | Python dependencies |

### Infrastructure
| File | Path | Description |
|------|------|-------------|
| nginx config | `/etc/nginx/conf.d/sijibintaro-id.conf` | Site config (proxy + auth) |
| htpasswd | `/etc/nginx/.wa-dashboard-htpasswd` | Basic auth (user: `siji`) |
| systemd unit | `sijibintaro-api.service` | Service definition |
| Database | `/opt/siji-dashboard/siji_database.db` | SQLite, 8.4MB |

## Features

### Pages

1. **Dashboard** - Overview cards (revenue, order status, SLA alerts, top products, customers, target), sales trend chart, order status donut, top services list, ongoing orders scroll
2. **Analytics** - Daily revenue bar chart, service category donut, payment status donut, monthly trend line chart
3. **Orders** - Filterable/searchable order table with pagination, click-to-detail modal
4. **Customers** - Three tabs:
   - *Overview* - Segment/frequency charts, top customers table, churn risk table (all clickable)
   - *Search* - Name/phone/address search with area dropdown filter
   - *Area Analysis* - Area cards grid with revenue/orders/growth, click for detail + LLM insight
5. **Settings** - Dashboard metadata and quick links

### Date Picker (Shared Component)
Three modes available on Dashboard, Analytics, and Orders pages:
- **Bulanan** - Monthly selector (dropdown, 72 months back)
- **Tahunan** - Year selector (2021-current)
- **Custom** - Date range (from/to inputs)

All date-filtered API endpoints accept: `month` (YYYY-MM), `year` (YYYY), `date_from`/`date_to` (YYYY-MM-DD).

### SLA Monitoring
Service-level benchmarks by type:
| Service Keyword | SLA (days) |
|----------------|-----------|
| Express / 24JAM | 1 |
| Reguler / Setrika / Laundry | 3 |
| Sepatu / Shoes | 5 |
| Tas / Bag | 7 |
| Karpet / Gordyn / Kasur | 8 |

Orders exceeding SLA are flagged as **overdue**; exceeding 2x SLA are **critical**.

### LLM Integration
- Model: `minimax-m2.5:cloud` via Ollama at `127.0.0.1:11434`
- Response time: ~6.5 seconds
- Context types: `area` (business analysis) and `customer` (CRM analysis)
- Graceful fallback if Ollama is unavailable
- Uses `urllib.request` (no extra dependencies)

## API Reference

All endpoints under prefix: `/api/dashboard/`

### Core Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/overview` | Revenue, orders, customers, SLA summary + alerts |
| GET | `/revenue/monthly` | Monthly revenue/orders (last 72 months) |
| GET | `/revenue/daily` | Daily revenue for selected period |
| GET | `/revenue/by-service` | Revenue by category and service |
| GET | `/orders` | Paginated order list (filter by status, search) |
| GET | `/orders/ongoing` | Active orders (not 100% or not picked up) |
| GET | `/products` | Product/service breakdown |
| GET | `/payment-status` | Payment distribution |
| GET | `/locations` | Top 20 locations by orders |

### Customer Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/customers/summary` | Total, active, HVC, churn stats + segments |
| GET | `/customers/top` | Top customers by lifetime value |
| GET | `/customers/hvc` | High-value customers (5+ orders or 1M+ spent) |
| GET | `/customers/churn-risk` | Customers at churn risk (30+ days inactive, 2+ orders) |
| GET | `/customers/frequency` | Order frequency distribution |
| GET | `/customers/search?q=&area=` | Search by name/phone/address, filter by area |
| GET | `/customers/detail?phone=` | Full customer profile with services, trend, orders |

### Area Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/areas/list` | All areas with order counts |
| GET | `/areas/analysis` | Per-area analytics with growth, recent activity, unpaid |

### LLM Endpoint
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/analysis/llm` | AI insight (body: `{context_type, data}`) |

### Utility
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check + transaction count |

### Legacy Compatibility
| GET | `/revenue` | Redirects to monthly/daily |
| GET | `/customers` | Combined summary + top |
| GET | `/hvc-churn` | Combined HVC + churn |

## Setup & Deployment

### Prerequisites
- Python 3.8+ with pip
- nginx
- Ollama (optional, for LLM insights)

### Install Dependencies
```bash
cd /root/sijibintaro-api
pip install -r requirements.txt
```

### Start API
```bash
systemctl start sijibintaro-api
# or manually:
cd /root/sijibintaro-api
uvicorn main:app --host 127.0.0.1 --port 8002
```

### Verify
```bash
# Health check
curl -s http://127.0.0.1:8002/api/dashboard/health

# Overview with SLA
curl -s http://127.0.0.1:8002/api/dashboard/overview?month=2026-02

# Customer search
curl -s "http://127.0.0.1:8002/api/dashboard/customers/search?q=hariza"

# Area analysis
curl -s http://127.0.0.1:8002/api/dashboard/areas/analysis

# LLM insight
curl -s -X POST http://127.0.0.1:8002/api/dashboard/analysis/llm \
  -H "Content-Type: application/json" \
  -d '{"context_type":"area","data":{"area":"Emerald","orders":4889}}'
```

### Restart After Changes
```bash
systemctl restart sijibintaro-api
```

### Nginx Config
Dashboard is served at `https://sijibintaro.id/dashboard/` with basic auth. API is proxied at `https://sijibintaro.id/api/dashboard/`.

## Rollback

### Frontend
```bash
cp /var/www/sijibintaro/dashboard/index.html.bak.20260222 /var/www/sijibintaro/dashboard/index.html
```

### Backend
Restore previous `dashboard_api.py` from backup or git history, then:
```bash
systemctl restart sijibintaro-api
```

## Changelog

### v2.2 (2026-02-22)
- **Dashboard:** MTD period label, order status badges (Proses/Siap/Selesai), SLA alert card, top products compact card
- **Orders:** Click any row to open detail modal (nota, customer, layanan, payment, progress, kasir)
- **Customers:** 3-tab layout (Overview / Search / Area Analysis)
  - Search: name/phone/address + area dropdown filter
  - Customer detail modal with profile, favorite services, order history, LLM insight
  - Area Analysis: area cards grid with revenue/growth, click for detail + LLM insight
- **Backend:** 6 new endpoints — `/customers/search`, `/customers/detail`, `/areas/list`, `/areas/analysis`, `/analysis/llm` (POST), SLA data in `/overview`
- **Modal system:** Shared modal overlay with Escape/click-outside close

### v2.1 (2026-02-22)
- Date picker component with 3 modes (Bulanan/Tahunan/Custom)
- All date-filtered endpoints support `month`, `year`, `date_from/date_to`
- Dark theme, sidebar + mobile bottom nav

### v2.0 (2026-02-22)
- Full rewrite from Streamlit to single-file HTML/JS + FastAPI
- Migrated from PostgreSQL to SQLite
- 5 pages: Dashboard, Analytics, Orders, Customers, Settings

### v1.0 (Legacy)
- Streamlit dashboard at port 8501
- PostgreSQL backend (now deprecated)
