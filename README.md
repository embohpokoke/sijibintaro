# Sijibintaro.id — Web Frontend

Static dashboard + FastAPI backend for SIJI Bintaro laundry business.

**URL:** https://sijibintaro.id | **Updated:** 2026-02-27

---

## Tech Stack

- **Frontend:** Static HTML/JS/CSS (`/var/www/sijibintaro/`)
- **Backend API:** FastAPI Python (`/root/sijibintaro-api/`) — port 8002
  - Service: `sijibintaro-api.service` (systemd)
- **Dashboard:** `/var/www/sijibintaro/dashboard/` → port 8001 (siji-dashboard.service)
- **Nginx config:** `/etc/nginx/conf.d/sijibintaro-id.conf`

---

## Common Commands

```bash
# API service
systemctl restart sijibintaro-api
systemctl status sijibintaro-api
journalctl -u sijibintaro-api -n 50 --no-pager

# Dashboard service  
systemctl restart siji-dashboard

# Deploy static files
rsync -av /local/path/ root@72.60.78.181:/var/www/sijibintaro/
```

---

## SSL

Dedicated cert at `/etc/letsencrypt/live/sijibintaro.id/` — expires 2026-05-28.

Previously used druygon.my.id cert (shared SAN) — fixed 2026-02-27.

---

*See /root/sijibintaro-api/README.md for full API documentation.*  
*Maintained by Asmuni (OpenClaw AI).*
