#!/bin/bash
# GOWA → PostgreSQL sync cron wrapper
# Runs every 15 minutes via crontab
# Also re-imports GOWA history JSON files once per day (at the first run after midnight)

cd /opt/sijibintaro
LOG=/var/log/siji-wa-sync.log
STAMP_FILE=/tmp/siji-wa-history-last-import

# Run regular GOWA API sync (recent messages)
/usr/bin/python3 wa_sync.py >> "$LOG" 2>&1

# Run full history import once per calendar day
TODAY=$(date +%Y-%m-%d)
LAST=$(cat "$STAMP_FILE" 2>/dev/null || echo "")
if [ "$LAST" != "$TODAY" ]; then
    echo "[$(date -Iseconds)] Running daily history import..." >> "$LOG"
    /usr/bin/python3 wa_history_import.py >> "$LOG" 2>&1
    echo "$TODAY" > "$STAMP_FILE"
fi
