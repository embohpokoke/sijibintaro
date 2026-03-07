#!/bin/bash
# GOWA → SQLite sync cron wrapper
# Runs every 15 minutes via crontab
cd /root/sijibintaro.id/api
/usr/bin/python3 wa_sync.py >> /var/log/siji-wa-sync.log 2>&1
