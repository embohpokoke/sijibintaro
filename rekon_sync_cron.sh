#!/bin/bash
# SIJI Rekon Sync Cron — parse invoices + payment signals dari GOWA SQLite
# Runs every 15 minutes via crontab
cd /opt/sijibintaro
/usr/bin/python3.12 rekon_sync.py >> /var/log/sijibintaro/rekon_sync.log 2>&1
