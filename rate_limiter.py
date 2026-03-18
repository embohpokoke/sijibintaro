# Rate limiter for SIJI autoreply - prevent spam per customer
from datetime import datetime, timedelta
from collections import defaultdict
import json

RATE_LIMIT_FILE = "/tmp/siji_rate_limit.json"
MAX_REPLIES_PER_HOUR = 10  # Max 10 autoreply per hour per customer

class RateLimiter:
    def __init__(self):
        self.limits = self._load()
    
    def _load(self):
        try:
            with open(RATE_LIMIT_FILE) as f:
                return json.load(f)
        except:
            return {}
    
    def _save(self):
        with open(RATE_LIMIT_FILE, 'w') as f:
            json.dump(self.limits, f)
    
    def check_and_log(self, phone_number: str) -> bool:
        """Check if phone can send autoreply. Returns True if allowed."""
        now = datetime.now()
        hour_key = now.strftime('%Y-%m-%d-%H')
        customer_key = f"{phone_number}:{hour_key}"
        
        count = self.limits.get(customer_key, 0)
        
        if count >= MAX_REPLIES_PER_HOUR:
            return False  # Rate limit exceeded
        
        self.limits[customer_key] = count + 1
        self._save()
        return True

limiter = RateLimiter()
