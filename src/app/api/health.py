"""
TaskPilot Health Check Endpoints
Инфраструктурный мониторинг доступности компонентов
"""

from fastapi import APIRouter, status
from pydantic import BaseModel
from typing import Dict, Any, Optional
import asyncio
import psycopg2
import redis
from app.config import settings

# ============================================================================
# Router Setup
# ============================================================================

router = APIRouter(prefix="/health", tags=["Health Checks"])

# ============================================================================
# Response Models
# ============================================================================

class HealthResponse(BaseModel):
    status: str
    components: Dict[str, bool]
    details: Optional[Dict[str, Any]] = {}
    timestamp: Optional[str] = None

class ComponentHealth(BaseModel):
    name: str
    status: str
    latency_ms: Optional[float] = None
    error: Optional[str] = None

# ============================================================================
# Health Check Functions
# ============================================================================

async def check_postgres() -> Dict[str, Any]:
    """Проверка подключения к PostgreSQL"""
    import time
    start = time.time()
    try:
        conn = psycopg2.connect(settings.DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        latency = (time.time() - start) * 1000
        return {"status": "healthy", "latency_ms": round(latency, 2)}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

async def check_redis() -> Dict[str, Any]:
    """Проверка подключения к Redis"""
    import time
    start = time.time()
    try:
        r = redis.from_url(settings.REDIS_URL)
        r.ping()
        latency = (time.time() - start) * 1000
        return {"status": "healthy", "latency_ms": round(latency, 2)}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

async def check_llm() -> Dict[str, Any]:
    """Проверка доступности Mistral API"""
    import time
    start = time.time()
    try:
        from mistralai.client import MistralClient
        client = MistralClient(api_key=settings.MISTRAL_API_KEY)
        await asyncio.wait_for(
            asyncio.to_thread(
                client.chat,
                model=settings.MISTRAL_MODEL,
                messages=[{"role": "user", "content": "ping"}]
            ),
            timeout=5.0
        )
        latency = (time.time() - start) * 1000
        return {"status": "healthy", "latency_ms": round(latency, 2)}
    except asyncio.TimeoutError:
        return {"status": "unhealthy", "error": "Timeout (5s)"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

# ============================================================================
# Endpoints
# ============================================================================

@router.get("/live", response_model=HealthResponse)
async def health_live():
    """
    Liveness Probe — процесс запущен
    
    Используется Kubernetes/Docker для проверки что контейнер работает.
    Не проверяет внешние зависимости.
    """
    from datetime import datetime
    return HealthResponse(
        status="healthy",
        components={"process": True},
        timestamp=datetime.utcnow().isoformat()
    )

@router.get("/ready", response_model=HealthResponse)
async def health_ready():
    """
    Readiness Probe — все зависимости доступны
    
    Используется для определения готовности принимать трафик.
    Проверяет: PostgreSQL, Redis, Mistral API
    """
    from datetime import datetime
    
    postgres_result = await check_postgres()
    redis_result = await check_redis()
    llm_result = await check_llm()
    
    postgres_ok = postgres_result["status"] == "healthy"
    redis_ok = redis_result["status"] == "healthy"
    llm_ok = llm_result["status"] == "healthy"
    
    all_healthy = all([postgres_ok, redis_ok, llm_ok])
    
    return HealthResponse(
        status="healthy" if all_healthy else "degraded",
        components={
            "postgres": postgres_ok,
            "redis": redis_ok,
            "llm": llm_ok
        },
        details={
            "postgres": postgres_result,
            "redis": redis_result,
            "llm": llm_result
        },
        timestamp=datetime.utcnow().isoformat()
    )

@router.get("/postgres", response_model=HealthResponse)
async def health_postgres():
    """Проверка только PostgreSQL"""
    from datetime import datetime
    result = await check_postgres()
    return HealthResponse(
        status=result["status"],
        components={"postgres": result["status"] == "healthy"},
        details={"postgres": result},
        timestamp=datetime.utcnow().isoformat()
    )

@router.get("/redis", response_model=HealthResponse)
async def health_redis():
    """Проверка только Redis"""
    from datetime import datetime
    result = await check_redis()
    return HealthResponse(
        status=result["status"],
        components={"redis": result["status"] == "healthy"},
        details={"redis": result},
        timestamp=datetime.utcnow().isoformat()
    )

@router.get("/llm", response_model=HealthResponse)
async def health_llm():
    """Проверка только Mistral API"""
    from datetime import datetime
    result = await check_llm()
    return HealthResponse(
        status=result["status"],
        components={"llm": result["status"] == "healthy"},
        details={"llm": result},
        timestamp=datetime.utcnow().isoformat()
    )

@router.get("/metrics/summary")
async def health_metrics_summary():
    """
    Краткая сводка для мониторинга
    
    Возвращает простой JSON для быстрого парсинга
    """
    postgres_ok = (await check_postgres())["status"] == "healthy"
    redis_ok = (await check_redis())["status"] == "healthy"
    
    return {
        "healthy": postgres_ok and redis_ok,
        "postgres": postgres_ok,
        "redis": redis_ok,
        "timestamp": asyncio.get_event_loop().time()
    }