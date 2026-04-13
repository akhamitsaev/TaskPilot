"""
TaskPilot FastAPI Application
API Gateway для чата и управления задачами
Интеграция: Metrics, Circuit Breaker, Celery, DB
"""

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, Field
from typing import Optional, List
import uuid
import structlog
import time

# Импорт конфигурации и БД
from app.config import settings
from app.db.engine import get_db, init_db
from app.db.task_repository import TaskRepository

# Импорт задач Celery
from app.worker.tasks import process_message

# Импорт инфраструктуры (Метрики)
from app.infrastructure.metrics import (
    MetricsMiddleware,
    get_metrics_response,
    setup_opentelemetry,
    update_circuit_breaker_metrics,
)

# Импорт инфраструктуры (Circuit Breaker)
from app.infrastructure.circuit_breaker import llm_circuit_breaker, CircuitState

# Импорт rate limiter
from app.infrastructure.rate_limiter import limiter, rate_limit_exception_handler

# Импорт auth router
from app.api.auth import router as auth_router

# Импорт health router
from app.api.health import router as health_router

logger = structlog.get_logger(__name__)

# ============================================================================
# FastAPI App Initialization
# ============================================================================

app = FastAPI(
    title="TaskPilot API",
    description="Intelligent Task Management System with LLM Agent",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# ============================================================================
# Middleware Setup
# ============================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(MetricsMiddleware)

app.state.limiter = limiter
app.add_exception_handler(429, rate_limit_exception_handler)

# ============================================================================
# Pydantic Models
# ============================================================================

class ChatMessageRequest(BaseModel):
    user_id: str = Field(..., description="UUID пользователя")
    group_id: str = Field(..., description="UUID группы")
    message: str = Field(..., min_length=1, max_length=2000, description="Текст сообщения")

class ChatMessageResponse(BaseModel):
    success: bool
    is_task: Optional[bool] = None
    task_id: Optional[str] = None
    task_title: Optional[str] = None
    response: str
    error: Optional[str] = None

class TaskSummary(BaseModel):
    id: str
    title: str
    status: str
    priority: int
    deadline: Optional[str] = None
    problem: Optional[str] = None

class TaskListResponse(BaseModel):
    tasks: List[TaskSummary]
    total: int

# ============================================================================
# Event Handlers
# ============================================================================

@app.on_event("startup")
async def startup_event():
    logger.info("taskpilot_api_starting")
    setup_opentelemetry()
    try:
        init_db()
        logger.info("database_initialized")
    except Exception as e:
        logger.error("database_init_failed", error=str(e))
    
    from app.infrastructure.circuit_breaker import CircuitState
    cb_state = llm_circuit_breaker.get_state()
    state_numeric = {
        CircuitState.CLOSED: 0,
        CircuitState.OPEN: 1,
        CircuitState.HALF_OPEN: 2
    }.get(cb_state, 0)
    update_circuit_breaker_metrics("llm", state_numeric)

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("taskpilot_api_shutting_down")

# ============================================================================
# Routes
# ============================================================================

app.include_router(auth_router)
app.include_router(health_router)

@app.get("/")
async def root():
    return {
        "service": "TaskPilot API",
        "version": "0.2.0",
        "status": "running",
        "docs": "/docs"
    }

@app.get("/health/ready")
async def health_ready(db: Session = Depends(get_db)):
    """Deep Health Check"""
    status = "healthy"
    components = {}
    
    try:
        # Исправлено: обёрнуто в text()
        db.execute(text("SELECT 1"))
        components["database"] = True
    except Exception as e:
        components["database"] = False
        status = "unhealthy"
        logger.warning("health_check_db_failed", error=str(e))
    
    cb_state = llm_circuit_breaker.get_state()
    components["llm_circuit_breaker"] = (cb_state == CircuitState.CLOSED)
    
    if cb_state != CircuitState.CLOSED:
        status = "degraded"
        
    return {
        "status": status,
        "components": components,
        "circuit_breaker_state": cb_state.value
    }

@app.get("/metrics")
async def metrics_endpoint():
    return get_metrics_response()

@app.post("/chat", response_model=ChatMessageResponse)
@limiter.limit("60/minute")
async def chat(
    request: Request,
    body: ChatMessageRequest,
    db: Session = Depends(get_db)
):
    try:
        user_id = uuid.UUID(body.user_id)
        group_id = uuid.UUID(body.group_id)
        
        logger.info("chat_request_received", user_id=str(user_id), msg_len=len(body.message))
        
        task = process_message.delay(
            user_id=str(user_id),
            group_id=str(group_id),
            message=body.message
        )
        
        result = task.get(timeout=35)
        
        return ChatMessageResponse(
            success=result.get("success", False),
            is_task=result.get("is_task"),
            task_id=result.get("task_id"),
            task_title=result.get("task_title"),
            response=result.get("response", ""),
            error=result.get("error")
        )
        
    except ValueError:   # <-- исправлено с uuid.UUIDError на ValueError
        raise HTTPException(status_code=400, detail="Invalid UUID format")
    
    except Exception as e:
        error_str = str(e)
        logger.error("chat_processing_failed", error=error_str)
        
        if "TimeoutError" in type(e).__name__ or "timeout" in error_str.lower():
            return ChatMessageResponse(
                success=False,
                response="Сервис перегружен. Попробуйте через минуту.",
                error="Task processing timeout"
            )
        
        if "CircuitBreakerOpenError" in type(e).__name__:
            return ChatMessageResponse(
                success=False,
                response="LLM сервис временно недоступен. Сработала защита.",
                error="Circuit Breaker Open"
            )

        raise HTTPException(status_code=500, detail=error_str)

@app.get("/tasks", response_model=TaskListResponse)
@limiter.limit("100/minute")
async def get_tasks(
    request: Request,
    user_id: str,
    group_id: str,
    limit: int = 20,
    db: Session = Depends(get_db)
):
    try:
        user_uuid = uuid.UUID(user_id)
        group_uuid = uuid.UUID(group_id)
        
        repo = TaskRepository(db)
        tasks = repo.get_user_tasks(user_uuid, group_uuid, limit=limit)
        
        return TaskListResponse(
            tasks=[
                TaskSummary(
                    id=str(t.id),
                    title=t.title,
                    status=t.status,
                    priority=t.priority,
                    deadline=t.deadline.isoformat() if t.deadline else None,
                    problem=t.problem
                )
                for t in tasks
            ],
            total=len(tasks)
        )
    except ValueError:   # <-- исправлено с uuid.UUIDError на ValueError
        raise HTTPException(status_code=400, detail="Invalid UUID format")
    except Exception as e:
        logger.error("get_tasks_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="info"
    )
