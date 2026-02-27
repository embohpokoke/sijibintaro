# PLAN.md - Project: SIJI Bintaro Dashboard v2.2

**Date:** 2026-02-22
**Author:** Claude (AI Agent)

## 1. Objective

Enhance SIJI Bintaro Dashboard from v2.1 to v2.2 with three improvements: (1) richer Dashboard page with MTD label, SLA alerts, and top products; (2) click-to-detail on Orders; (3) major Customer module upgrade with search, area analysis, and LLM-powered insights.

## 2. Scope & Boundaries

### In Scope
- Add SLA monitoring to `/overview` endpoint with overdue/critical detection
- New API endpoints: `/customers/search`, `/customers/detail`, `/areas/list`, `/areas/analysis`, `/analysis/llm`
- Frontend modal system for order/customer/area detail views
- Dashboard page: MTD label, order status badges, SLA alert card, top products card
- Orders page: clickable rows with detail modal
- Customers page: 3-tab layout (Overview, Search, Area Analysis)
- LLM integration via Ollama for area and customer insights

### Out of Scope
- Database schema changes (read-only analytics)
- Authentication changes (existing nginx basic auth retained)
- Mobile app or PWA capabilities
- Export/download functionality
- Real-time WebSocket updates
- Notification system (WhatsApp alerts for SLA)

## 3. Assumptions

- SQLite database at `/opt/siji-dashboard/siji_database.db` is the single source of truth (13,180 transactions)
- Database has `normalized_area` column populated for area analysis (32 areas confirmed)
- Ollama is running at `127.0.0.1:11434` with `minimax-m2.5:cloud` model loaded
- If Ollama is unavailable, LLM features degrade gracefully (show fallback message)
- Frontend is served as single-file HTML (no build step, no npm)
- Feb 2026 `group_layanan` is NULL — API falls back to `nama_layanan`

## 4. Deliverables

| # | Deliverable | File |
|---|-------------|------|
| 1 | Backend: SLA alerts in `/overview` + 5 new endpoints + 1 POST endpoint | `/root/sijibintaro-api/dashboard_api.py` |
| 2 | Frontend: Modal system (CSS + HTML + JS) | `/var/www/sijibintaro/dashboard/index.html` |
| 3 | Frontend: Enhanced Dashboard page (MTD, SLA, badges, top products) | `index.html` |
| 4 | Frontend: Orders click-to-detail | `index.html` |
| 5 | Frontend: Customer tabs (Overview, Search, Area Analysis) | `index.html` |
| 6 | Documentation: README.md, PLAN-v2.2.md | `/var/www/sijibintaro/dashboard/` |

## 5. Acceptance Criteria

### Backend Verification
```bash
# 1. Health check passes
curl -s http://127.0.0.1:8002/api/dashboard/health
# Expected: {"status":"ok","database":"sqlite","transactions":13180}

# 2. Overview includes SLA data
curl -s http://127.0.0.1:8002/api/dashboard/overview?month=2026-02 | python3 -c "
import sys,json;d=json.load(sys.stdin)
assert 'sla_summary' in d, 'Missing sla_summary'
assert 'sla_alerts' in d, 'Missing sla_alerts'
print('PASS: SLA data present')
print('  Ongoing:', d['sla_summary']['total_ongoing'])
print('  Overdue:', d['sla_summary']['overdue'])
"

# 3. Customer search returns results
curl -s "http://127.0.0.1:8002/api/dashboard/customers/search?q=hariza" | python3 -c "
import sys,json;d=json.load(sys.stdin)
assert d['count'] >= 1, 'No results for hariza'
assert d['results'][0]['customer_name'] == 'Bu Hariza'
print('PASS: Customer search works')
"

# 4. Customer detail returns profile
curl -s "http://127.0.0.1:8002/api/dashboard/customers/detail?phone=628170070699" | python3 -c "
import sys,json;d=json.load(sys.stdin)
assert d['summary']['total_orders'] == 234
assert len(d['top_services']) > 0
assert len(d['recent_orders']) > 0
print('PASS: Customer detail works')
"

# 5. Area list returns 32 areas
curl -s http://127.0.0.1:8002/api/dashboard/areas/list | python3 -c "
import sys,json;d=json.load(sys.stdin)
assert len(d['areas']) == 32, f'Expected 32 areas, got {len(d[\"areas\"])}'
print('PASS: Area list works (' + str(len(d['areas'])) + ' areas)')
"

# 6. Area analysis returns growth data
curl -s http://127.0.0.1:8002/api/dashboard/areas/analysis | python3 -c "
import sys,json;d=json.load(sys.stdin)
a = d['areas'][0]
assert 'growth_pct' in a, 'Missing growth_pct'
assert 'unpaid' in a, 'Missing unpaid'
print('PASS: Area analysis works')
print('  Top area:', a['area'], '| Revenue:', a['revenue'])
"

# 7. LLM endpoint responds (even if Ollama is down)
curl -s -X POST http://127.0.0.1:8002/api/dashboard/analysis/llm \
  -H "Content-Type: application/json" \
  -d '{"context_type":"area","data":{"area":"Emerald","orders":4889}}' | python3 -c "
import sys,json;d=json.load(sys.stdin)
assert 'insight' in d, 'Missing insight'
assert 'status' in d, 'Missing status'
print('PASS: LLM endpoint responds, status:', d['status'])
"
```

### Frontend Verification
1. Load `https://sijibintaro.id/dashboard/` - Dashboard page shows MTD label, 6 cards, SLA badge
2. Click "Orders" nav - table loads, click any row - modal opens with full detail
3. Click "Customers" nav - 3 tabs visible (Overview / Search / Area Analysis)
4. Click "Search" tab - search "hariza" - results appear, click row - customer detail modal with LLM insight
5. Click "Area Analysis" tab - area cards load, click "Emerald" - area modal opens with LLM insight
6. Test mobile (resize to 480px) - bottom nav works, modals are responsive, area cards stack

## 6. Timeline

| Phase | Description | Duration |
|-------|-------------|----------|
| Phase 1 | Backend: New endpoints + SLA | ~15 min |
| Phase 2 | Frontend: Modal CSS + HTML | ~5 min |
| Phase 3 | Frontend: Dashboard enhancements | ~10 min |
| Phase 4 | Frontend: Orders click-to-detail | ~5 min |
| Phase 5 | Frontend: Customer module (tabs, search, area, LLM) | ~20 min |
| Phase 6 | Restart, QA, verification | ~10 min |
| **Total** | | **~65 min** |

## 7. Risks & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Ollama down or slow | LLM insights show fallback text | Graceful error handling: `"Insight AI sedang tidak tersedia"` message; all other features unaffected |
| Large database queries | Slow area analysis (32 areas x 4 queries each) | Analysis endpoint loads once, cached in frontend `_areasData`; no re-fetch on tab switch |
| Feb 2026 missing `group_layanan` | Service names show wrong | `COALESCE(group_layanan, nama_layanan, 'Lainnya')` pattern used everywhere |
| Single-file HTML grows too large | Hard to maintain | Currently 1,760 lines; still manageable. If exceeds 2,500, consider splitting JS |
| Frontend backup not created | Can't rollback | Backup exists: `index.html.bak.20260222` |

## 8. Dependencies

- Python FastAPI + uvicorn (already installed)
- SQLite database populated with transaction data (already present)
- nginx configured for `/dashboard/` and `/api/dashboard/` (already configured)
- Ollama running with `minimax-m2.5:cloud` (optional, graceful fallback)
- No new Python packages required (`urllib.request` is stdlib)

## 9. Attachments/References

- **README.md:** `/var/www/sijibintaro/dashboard/README.md` — full project documentation
- **Frontend:** `/var/www/sijibintaro/dashboard/index.html` — single-file dashboard
- **Backend:** `/root/sijibintaro-api/dashboard_api.py` — API router
- **Database:** `/opt/siji-dashboard/siji_database.db` — SQLite (8.4MB, 13,180 tx)
- **Memory:** `/root/.claude/projects/-root/memory/MEMORY.md` — project context
- **Backup:** `/var/www/sijibintaro/dashboard/index.html.bak.20260222` — v2.1 backup
