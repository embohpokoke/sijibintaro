#!/usr/bin/env python3
import sqlite3
import json
import requests
from datetime import datetime
import sys

DB_PATH = '/var/www/sijibintaro/siji.db'
DEEPSEEK_API = 'https://api.deepseek.com/v1/chat/completions'
DEEPSEEK_KEY = 'sk-101e909db66846a2b84cc7f9479f58f6'
MINIONS_API = 'https://minions.embohpokoke.my.id/api/logs'

def get_metrics(hours=1):
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    c = db.cursor()
    c.execute(f'''SELECT COUNT(*) as msgs, COUNT(DISTINCT user_id) as users,
    AVG(response_time_ms) as avg_ms FROM conversations 
    WHERE created_at >= datetime('now', '-{hours} hours')''')
    r = dict(c.fetchone())
    db.close()
    return r

def sample(limit=3):
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    c = db.cursor()
    c.execute(f'SELECT user_msg, bot_response FROM conversations WHERE created_at >= datetime("now", "-2 hours") ORDER BY created_at DESC LIMIT {limit}')
    samples = [dict(r) for r in c.fetchall()]
    db.close()
    return samples

def score(user_msg, bot_response):
    try:
        r = requests.post(DEEPSEEK_API, headers={'Authorization': f'Bearer {DEEPSEEK_KEY}'}, json={
            'model': 'deepseek-chat', 'messages': [{'role': 'user', 'content': f'Rate this (1-5) - Relevance/Helpfulness/Accuracy: User: {user_msg[:50]} Bot: {bot_response[:50]}. JSON only: {{"score": X}}'}]
        }, timeout=3)
        if r.status_code == 200:
            try:
                return json.loads(r.json()['choices'][0]['message']['content']).get('score', 3)
            except:
                return 3
        return 3
    except:
        return 3

def main():
    m = get_metrics(1)
    s = sample(3)
    
    scores = [score(x['user_msg'], x['bot_response']) for x in s]
    avg_q = sum(scores) / len(scores) if scores else 3
    
    msg = f"📊 SIJI Hour {datetime.now().strftime('%H:%M WIB')}\n"           f"Messages: {m.get('msgs', 0)} | Users: {m.get('users', 0)} | Quality: {avg_q:.1f}/5"
    
    try:
        requests.post(MINIONS_API, json={'agent_id': 'bob', 'level': 'info', 'message': msg}, timeout=3)
        print('OK')
    except:
        print('FAIL')

if __name__ == '__main__':
    main()
