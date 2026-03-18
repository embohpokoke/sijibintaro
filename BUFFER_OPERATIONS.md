# Buffer Operations Guide — SIJI Automation

**Location:** `/root/sijibintaro.id/api/`
**Created:** 2026-03-14
**Status:** Production-ready (testing phase starts Mon 3/16)

---

## Quick Reference

### Run Scripts Manually

```bash
# Auto-post (generate Ideas with captions)
cd /root/siji-buffer-automation
python3 scripts/siji_buffer_autopost.py

# Monitor (daily health check)
python3 scripts/siji_buffer_monitor.py

# Report (weekly summary)
python3 scripts/siji_buffer_report.py
```

### Check Logs

```bash
# Recent autopost activity
tail -f /root/siji-buffer-automation/logs/buffer_autopost.log

# Monitor status
cat /root/siji-buffer-automation/logs/buffer_monitor.log

# Weekly report
cat /root/siji-buffer-automation/logs/buffer_report.log
```

### View Config

```bash
# API credentials + channel IDs
cat /root/siji-buffer-automation/config/.env.buffer
```

---

## Automation Schedule (Cron)

**Active from 2026-03-20 onwards:**

```bash
# Sun 20:00 WIB — Auto-generate Ideas
0 13 * * 0 cd /root/siji-buffer-automation && python3 scripts/siji_buffer_autopost.py >> logs/buffer_autopost.log 2>&1

# Daily 14:00 WIB — Health check
0 7 * * * cd /root/siji-buffer-automation && python3 scripts/siji_buffer_monitor.py >> logs/buffer_monitor.log 2>&1

# Monday 17:00 WIB — Weekly report
0 10 * * 1 cd /root/siji-buffer-automation && python3 scripts/siji_buffer_report.py >> logs/buffer_report.log 2>&1
```

---

## Weekly Workflow (Dono)

### Sunday (Posting Day) — 20:00 WIB

```
20:00 WIB — Script runs automatically
           └─ Generates Idea with caption
           └─ Selects random media

20:15 WIB — Dono checks log
           └─ tail -f logs/buffer_autopost.log
           └─ Get: Idea ID, caption, media file

20:30 WIB — Go to Buffer.com
           └─ Drafts section
           └─ Find the new Idea

20:40 WIB — Add image to Idea
           └─ Select from media library or upload

20:50 WIB — Schedule/Post
           └─ Schedule for specific time OR
           └─ Post immediately to IG + TikTok

21:00 WIB — Done! (~15 minutes total)
```

### Monday (Monitoring) — 14:00 WIB

```
14:00 WIB — Monitor script runs
           └─ Checks channel health
           └─ Verifies queue status
           └─ Checks org limits

14:15 WIB — Dono reviews monitor log
           └─ cat logs/buffer_monitor.log
           └─ Look for: any errors, disconnections, queue paused?

17:00 WIB — Report script runs
           └─ Summarizes posts from last 7 days
           └─ Extracts engagement metrics

17:15 WIB — Dono reviews report
           └─ cat logs/buffer_report.log
           └─ Check: posts visible? metrics reasonable?

17:30 WIB — Done! (~5 minutes total)
```

---

## Buffer API Reference (Quick)

### Endpoint
```
https://api.buffer.com
Authorization: Bearer <TOKEN>
```

### Key Mutations

**Create Idea (what autopost uses):**
```graphql
mutation CreateIdea {
  createIdea(input: {
    organizationId: "69b39c8a4eae0eec66762adc"
    content: {
      title: "SIJI Weekly"
      text: "<caption>"
      media: [{url: "<image_url>", alt: "SIJI"}]
      services: [instagram, tiktok]
    }
  }) {
    ... on Idea {
      id
      content { text }
    }
    ... on MutationError {
      message
    }
  }
}
```

**Create Post (what monitor uses to track):**
```graphql
query GetScheduledPosts {
  posts(input: {
    organizationId: "69b39c8a4eae0eec66762adc"
    filter: {
      status: [scheduled, sent]
      channelIds: ["69b39cc47be9f8b1714f3d0c", "69b39d147be9f8b1714f3e34"]
    }
  }) {
    edges {
      node {
        id
        text
        dueAt
        sentAt
        externalLink
      }
    }
  }
}
```

---

## Configuration

### File: `.env.buffer`

```
BUFFER_API_TOKEN=hLn1qQ5JoU8G0N143B9xeEDPdQouo0kO9piq7B_1nC0
BUFFER_IG_ID=69b39cc47be9f8b1714f3d0c
BUFFER_TIKTOK_ID=69b39d147be9f8b1714f3e34
BUFFER_ORG_ID=69b39c8a4eae0eec66762adc
```

**Never share credentials!**

---

## Troubleshooting

### Issue: "Idea not created" (autopost fails)

```
Check:
1. API token valid? cat .env.buffer
2. Org ID correct? (should be 69b39c8a4eae0eec66762adc)
3. Channels connected in Buffer.com?
4. API rate limit? (check HTTP response code)

Fix:
- Verify .env.buffer loaded correctly
- Check internet connection
- Retry script manually
- Check Buffer status page
```

### Issue: "Monitor returns no data"

```
Check:
1. Channels connected in Buffer.com?
2. Any posts scheduled yet?
3. GraphQL query syntax correct?

Fix:
- Visit Buffer.com → Channels
- Verify Instagram + TikTok connected
- Check channel IDs in config
```

### Issue: "Cron jobs not running"

```
Check:
1. Cron installed? which cron
2. Cron running? systemctl status cron
3. Crontab correct? crontab -l
4. Script path correct?
5. Permissions? ls -la /root/siji-buffer-automation/scripts/

Fix:
- Install cron: apt-get install cron
- Start cron: systemctl start cron
- Edit crontab: crontab -e
- Verify paths absolute (not relative)
- Check script permissions (should be 755)
```

### Issue: "Images not downloading"

```
Check:
1. Media folder exists? ls /var/www/sijibintaro/media/
2. Images in folder?
3. File permissions?

Fix:
- Verify media folder path
- Check image file types (jpg, png)
- Ensure read permissions
```

---

## Buffer GraphQL Playground

**Test queries before deploying:**

```
https://api.buffer.com/graphql
```

**Header:**
```
Authorization: Bearer <TOKEN>
```

**Test query:**
```graphql
query TestConnection {
  account {
    id
    email
  }
}
```

If returns account info → connection OK ✅

---

## Performance Metrics

### Expected Load

```
autopost:  ~2 seconds (network call to Buffer)
monitor:   ~5 seconds (3 GraphQL queries)
report:    ~3 seconds (1 query, light processing)

Total weekly: ~30 seconds of API calls
```

### API Limits

```
Buffer free plan:
- 10 scheduled posts per channel (we use 4/month → safe)
- 100 Ideas (we use ~4/month → safe)
- No rate limits documented (but respect good practice)
```

---

## Monitoring Checklist

**Weekly (Dono):**
- [ ] Check autopost log (Sun evening)
- [ ] Verify image added to Buffer
- [ ] Confirm post visible on IG + TikTok
- [ ] Review monitor log (Mon afternoon)
- [ ] Review report summary (Mon evening)
- [ ] Any errors? Document them.

**Monthly (Dono + Erik):**
- [ ] Review engagement metrics
- [ ] Any issues emerged?
- [ ] Optimization suggestions?
- [ ] Plan next month's captions?

---

## Backup & Recovery

### Backup Config

```bash
# Backup credentials
cp /root/siji-buffer-automation/config/.env.buffer \
   /root/siji-buffer-automation/config/.env.buffer.backup
```

### Backup Logs

```bash
# Archive old logs monthly
tar -czf /root/siji-buffer-automation/logs/archive/logs-2026-03.tar.gz \
    /root/siji-buffer-automation/logs/*.log
```

### Restore from Backup

```bash
# If script broke, restore config
cp /root/siji-buffer-automation/config/.env.buffer.backup \
   /root/siji-buffer-automation/config/.env.buffer
```

---

## Next Steps

### Monday 2026-03-16

- [ ] Run siji_buffer_autopost.py manually (test)
- [ ] Run siji_buffer_monitor.py manually (test)
- [ ] Run siji_buffer_report.py manually (test)
- [ ] Deploy cron jobs if all pass

### Sunday 2026-03-20

- [ ] First automated posting at 20:00 WIB
- [ ] Manual workflow: add image + post
- [ ] Begin 2-week validation

### Friday 2026-03-28

- [ ] Review entire testing week
- [ ] Go/No-Go decision
- [ ] If ✅: Production goes live Tue 4/1

---

## Support

**Questions?**
- Check logs: `/root/siji-buffer-automation/logs/`
- Read guides: `/root/siji-buffer-automation/docs/`
- Test manually before relying on cron
- Document any issues found

**Emergency Contact:**
- Dono (@embohwesbot on Telegram)

---

*Keep it simple. Keep it running. SIJI automation is maintenance mode.*
