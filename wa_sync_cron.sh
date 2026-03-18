#!/bin/bash
# GOWA → SQLite sync cron wrapper
# Runs every 15 minutes via crontab
cd /var/www/sijibintaro
/usr/bin/python3 wa_sync.py >> /var/log/siji-wa-sync.log 2>&1
