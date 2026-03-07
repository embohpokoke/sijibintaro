"""Cache decorator for FastAPI endpoints"""
from functools import wraps
from cache_manager import cache
import inspect


def cached(ttl: int = 300, prefix: str = None):
    """
    Decorator to cache FastAPI endpoint results
    
    Args:
        ttl: Time-to-live in seconds (default: 5 minutes)
        prefix: Cache key prefix (defaults to function name)
    """
    def decorator(func):
        cache_prefix = prefix or func.__name__
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract cache key from function parameters
            sig = inspect.signature(func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            
            # Use all non-self parameters for cache key
            cache_params = {k: v for k, v in bound.arguments.items() if k != 'self'}
            
            # Check cache
            cached_result = cache.get(cache_prefix, **cache_params)
            if cached_result is not None:
                return cached_result
            
            # Execute function
            result = await func(*args, **kwargs)
            
            # Store in cache
            cache.set(cache_prefix, result, ttl=ttl, **cache_params)
            
            return result
        
        return wrapper
    return decorator
