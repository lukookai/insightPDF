import time
import asyncio
from functools import wraps
from loguru import logger

def timer(func):
    """
    Decorator: log execution time of both async and sync functions.
    """
    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.time()
            try:
                return await func(*args, **kwargs)
            finally:
                elapsed = time.time() - start
                logger.info(f"[TIMER] {func.__name__} took {elapsed:.3f}s")
        return async_wrapper
    else:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.time()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed = time.time() - start
                logger.info(f"[TIMER] {func.__name__} took {elapsed:.3f}s")
        return sync_wrapper
