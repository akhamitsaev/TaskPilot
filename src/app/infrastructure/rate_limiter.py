"""
TaskPilot Rate Limiter
Ограничение запросов через Redis (Token Bucket)
"""

import time
from functools import wraps
from fastapi import Request, HTTPException, status
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.config import settings
import redis
import structlog

logger = structlog.get_logger(__name__)

# ============================================================================
# Limiter Configuration
# ============================================================================

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
    storage_uri=settings.REDIS_URL,
    strategy="fixed-window"
)

# ============================================================================
# Custom Rate Limit Exceeded Handler
# ============================================================================

async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded):
    logger.warning(
        "rate_limit_exceeded",
        path=request.url.path,
        client=request.client.host,
        limit=str(exc.detail)
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Rate limit exceeded. Please try again later.",
        headers={"Retry-After": "60"}
    )

# ============================================================================
# Custom Decorator (для сложных сценариев)
# ============================================================================

def rate_limit_by_user(limit: str = "100/minute"):
    """
    Декоратор для ограничения запросов по пользователю (через JWT)
    
    Usage:
        @app.post("/chat")
        @rate_limit_by_user("60/minute")
        async def chat(...):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request: Request = kwargs.get('request')
            if request:
                # Попытка получить user_id из заголовка
                user_id = request.headers.get("X-User-ID", get_remote_address())
                # Временное переключение ключа
                original_key_func = limiter._key_func
                limiter._key_func = lambda _: user_id
                try:
                    result = await func(*args, **kwargs)
                finally:
                    limiter._key_func = original_key_func
                return result
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# ============================================================================
# Redis-based Rate Limit Counter (для метрик)
# ============================================================================

class RateLimitMetrics:
    """Счётчик для метрик rate limiting"""
    
    def __init__(self, redis_url: str):
        try:
            self.redis = redis.from_url(redis_url)
            self.redis.ping()
        except:
            self.redis = None
    
    def record_request(self, endpoint: str, user_id: str, allowed: bool):
        """Запись запроса для метрик"""
        if not self.redis:
            return
        
        try:
            timestamp = int(time.time() // 60)  # По минутам
            key = f"ratelimit:metrics:{endpoint}:{timestamp}"
            
            self.redis.hincrby(key, "total", 1)
            if not allowed:
                self.redis.hincrby(key, "rejected", 1)
            
            self.redis.expire(key, 3600)  # TTL 1 час
        except Exception as e:
            logger.error("rate_limit_metrics_error", error=str(e))
    
    def get_stats(self, endpoint: str, minutes: int = 60) -> dict:
        """Получение статистики за последние N минут"""
        if not self.redis:
            return {"total": 0, "rejected": 0}
        
        total = 0
        rejected = 0
        now = int(time.time() // 60)
        
        for i in range(minutes):
            key = f"ratelimit:metrics:{endpoint}:{now - i}"
            stats = self.redis.hgetall(key)
            if stats:
                total += int(stats.get(b"total", 0))
                rejected += int(stats.get(b"rejected", 0))
        
        return {"total": total, "rejected": rejected, "rejection_rate": rejected / max(total, 1)}

# Global instance
rate_limit_metrics = RateLimitMetrics(settings.REDIS_URL)