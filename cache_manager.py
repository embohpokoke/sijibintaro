"""
In-memory caching layer for SIJI Dashboard API
TTL-based cache with automatic invalidation
"""

from datetime import datetime, timedelta
from typing import Any, Optional, Dict
import hashlib
import json


class CacheManager:
    def __init__(self, default_ttl: int = 300):
        """
        Initialize cache manager
        
        Args:
            default_ttl: Default time-to-live in seconds (default: 5 minutes)
        """
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.default_ttl = default_ttl
        self.stats = {
            'hits': 0,
            'misses': 0,
            'invalidations': 0
        }
    
    def _generate_key(self, prefix: str, **kwargs) -> str:
        """Generate cache key from prefix and parameters"""
        params_str = json.dumps(kwargs, sort_keys=True)
        hash_suffix = hashlib.md5(params_str.encode()).hexdigest()[:8]
        return f"{prefix}:{hash_suffix}"
    
    def get(self, prefix: str, **kwargs) -> Optional[Any]:
        """
        Get cached value if exists and not expired
        
        Args:
            prefix: Cache key prefix
            **kwargs: Parameters to include in cache key
            
        Returns:
            Cached value or None if expired/not found
        """
        key = self._generate_key(prefix, **kwargs)
        
        if key in self._cache:
            entry = self._cache[key]
            if datetime.now() < entry['expires_at']:
                self.stats['hits'] += 1
                return entry['value']
            else:
                # Expired - remove from cache
                del self._cache[key]
                self.stats['invalidations'] += 1
        
        self.stats['misses'] += 1
        return None
    
    def set(self, prefix: str, value: Any, ttl: Optional[int] = None, **kwargs):
        """
        Set cache value with TTL
        
        Args:
            prefix: Cache key prefix
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if None)
            **kwargs: Parameters to include in cache key
        """
        key = self._generate_key(prefix, **kwargs)
        ttl = ttl or self.default_ttl
        
        self._cache[key] = {
            'value': value,
            'expires_at': datetime.now() + timedelta(seconds=ttl),
            'created_at': datetime.now()
        }
    
    def invalidate(self, prefix: Optional[str] = None):
        """
        Invalidate cache entries
        
        Args:
            prefix: If provided, only invalidate keys with this prefix.
                   If None, clear entire cache.
        """
        if prefix is None:
            count = len(self._cache)
            self._cache.clear()
            self.stats['invalidations'] += count
        else:
            keys_to_delete = [k for k in self._cache.keys() if k.startswith(prefix)]
            for key in keys_to_delete:
                del self._cache[key]
            self.stats['invalidations'] += len(keys_to_delete)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total_requests = self.stats['hits'] + self.stats['misses']
        hit_rate = (self.stats['hits'] / total_requests * 100) if total_requests > 0 else 0
        
        return {
            'hits': self.stats['hits'],
            'misses': self.stats['misses'],
            'invalidations': self.stats['invalidations'],
            'hit_rate': round(hit_rate, 2),
            'cache_size': len(self._cache),
            'total_requests': total_requests
        }
    
    def cleanup_expired(self):
        """Remove all expired entries"""
        now = datetime.now()
        keys_to_delete = [
            k for k, v in self._cache.items() 
            if now >= v['expires_at']
        ]
        for key in keys_to_delete:
            del self._cache[key]
        self.stats['invalidations'] += len(keys_to_delete)
        return len(keys_to_delete)


# Global cache instance
cache = CacheManager(default_ttl=300)  # 5 minutes default
