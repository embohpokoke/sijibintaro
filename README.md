# SIJI Bintaro API

FastAPI backend for the SIJI Bintaro laundry business. Revenue ~23M IDR/month.

**Service:** `sijibintaro-api.service` | **Port:** 8002 | **Updated:** 2026-02-27

---

## File Structure

```
/root/sijibintaro-api/
  main.py                  # FastAPI entry point (port 8002)
  wa_webhook.py            # WhatsApp webhook (Fonnte + GOWA)
  dashboard_api.py         # Dashboard analytics API
  siji_daily_digest.py     # Daily WA summary cron (20:00 WIB)
  smartlink_importer.py    # Smartlink XLSX import
  siji.db                  # SQLite: WA conversations & customers
  notify_lamaran.py        # Lamaran notification handler
  send_lamaran_recap.py    # Lamaran recap sender
  address_normalizer.py    # Address normalization util
  product_mapping.py       # Product name mapping
  location_references.json # Location reference data
  requirements.txt
```

---

## Databases

| DB | Path | Tables |
|----|------|--------|
| Transactions | `/opt/siji-dashboard/siji_database.db` | `transactions`, `import_log` (~13,220 rows) |
| WhatsApp | `/root/sijibintaro-api/siji.db` | `wa_conversations`, `wa_customers` |

---

## Key API Endpoints

```
POST /api/wa/webhook/Al6ZNtAz     # Fonnte inbound webhook
POST /api/wa/gowa-webhook          # GOWA inbound/outbound webhook
GET  /api/dashboard/overview       # KPI summary
GET  /api/dashboard/revenue/daily  # Daily revenue
GET  /api/wa/conversations         # WA inbox
```

---

## Common Commands

```bash
# Service management
systemctl restart sijibintaro-api
systemctl status sijibintaro-api
journalctl -u sijibintaro-api -n 50 --no-pager

# Import Smartlink XLSX
python3 /tmp/siji_importer_sqlite.py

# Test webhook
curl -s -X POST https://sijibintaro.id/api/wa/webhook/Al6ZNtAz \
  -H 'Content-Type: application/json' \
  -d '{"sender":"62811319003","message":"test","device":"6281288783088"}'

# Manual daily digest
python3 /root/sijibintaro-api/siji_daily_digest.py
```

---

## WhatsApp Setup

- **WA Number:** 6281288783088 (customer-facing)
- **Fonnte token:** WYc5uxbS8JB7EpbcK3DS
- **GOWA:** port 3002 at `https://sijibintaro.id/gowa/`
  - Login: `siji` / `sijiwa2026` | Secret: `sijigowaSecret2026`
- **AUTOREPLY_ENABLED:** False (listen-only)
- **Admin notifications:** Erik (62811319003) + Ocha (628118606999)
- **Telegram:** `siji.bintaro.report1` → chat_id 5309429603

---

## Dashboard

- URL: `https://sijibintaro.id/dashboard/`
- Auth: `siji` / `siji2026admin`
- Target: 40M IDR/month

---

## Cron Jobs

```
0 13 * * * python3 /root/sijibintaro-api/siji_daily_digest.py  # 20:00 WIB
```

---

## Deployment

```bash
# 1. Edit files locally (Mac mini ~/clawd/sijibintaro-api/)
# 2. Deploy via rsync
rsync -av /local/path/ root@72.60.78.181:/root/sijibintaro-api/
# 3. Restart service
systemctl restart sijibintaro-api
# 4. Verify
curl -s https://sijibintaro.id/api/dashboard/overview
```

---

## Known Issues / RCA

### 2026-02-22 — Service Crash (Resolved)

**Root cause:** `app.include_router(dashboard_router)` in `main.py` referenced an undefined router.

**Fix:** Removed invalid router line from `main.py`, restarted service.

**Prevention:**
- Run `python -m py_compile main.py` before restarting
- Check for undefined names with `ruff` or `flake8 --select=F821`
- Verify router imports are all defined before registering

---

*Maintained by Asmuni (OpenClaw AI). Update this file after infrastructure changes.*
