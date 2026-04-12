"""
TaskPilot Celery Application
Конфигурация асинхронной очереди задач
"""

from celery import Celery
from app.config import settings

# ============================================================================
# Celery Application
# ============================================================================

celery_app = Celery(
    'taskpilot',
    broker=settings.celery_broker,
    backend=settings.celery_backend,
    include=[
        'app.worker.tasks',  # Задачи будут в этом модуле
    ]
)

# ============================================================================
# Celery Configuration
# ============================================================================

celery_app.conf.update(
    # Серийность задач
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    
    # Надёжность (Infrastructure Track)
    task_acks_late=settings.CELERY_TASK_ACKS_LATE,
    task_reject_on_worker_lost=settings.CELERY_TASK_REJECT_ON_WORKER_LOST,
    task_track_started=True,
    
    # Производительность
    worker_concurrency=settings.CELERY_WORKER_CONCURRENCY,
    worker_prefetch_multiplier=1,
    
    # Retry логика
    task_default_retry_delay=30,
    task_max_retries=3,
    
    # Таймауты
    task_soft_time_limit=300,
    task_time_limit=600,
    
    # Очереди
    task_default_queue='taskpilot_tasks',
    task_queues={
        'taskpilot_tasks': {
            'exchange': 'taskpilot',
            'routing_key': 'default',
        },
    },
    
    # Rate limiting (защита LLM API)
    task_default_rate_limit='100/m',
    
    # Логирование
    worker_hijack_root_logger=False,
    worker_log_level=settings.LOG_LEVEL,
)

# ============================================================================
# Celery Beat (Планировщик периодических задач)
# ============================================================================

celery_app.conf.beat_schedule = {
    # Пример: очистка старых сессий каждые 60 минут
    # 'cleanup-sessions': {
    #     'task': 'app.worker.tasks.cleanup_sessions',
    #     'schedule': 3600.0,
    # },
}

# ============================================================================
# Signals (для наблюдаемости)
# ============================================================================

from celery import signals
import structlog

logger = structlog.get_logger(__name__)

@signals.worker_init.connect
def worker_init_handler(sender, **kwargs):
    """Инициализация воркера"""
    logger.info("celery_worker_init", 
                concurrency=settings.CELERY_WORKER_CONCURRENCY,
                broker=settings.celery_broker)

@signals.task_prerun.connect
def task_prerun_handler(task_id, task, *args, **kwargs):
    """Перед выполнением задачи"""
    logger.info("task_started", 
                task_id=task_id, 
                task_name=task.name)

@signals.task_postrun.connect
def task_postrun_handler(task_id, task, *args, retval=None, state=None, **kwargs):
    """После выполнения задачи"""
    logger.info("task_completed", 
                task_id=task_id, 
                task_name=task.name,
                state=state,
                has_result=retval is not None)

@signals.task_failure.connect
def task_failure_handler(task_id, exception, *args, **kwargs):
    """При сбое задачи"""
    logger.error("task_failed", 
                 task_id=task_id, 
                 exception=str(exception))

# ============================================================================
# Main (для тестирования)
# ============================================================================

if __name__ == '__main__':
    print("=== TaskPilot Celery Configuration ===")
    print(f"Broker: {settings.celery_broker}")
    print(f"Backend: {settings.celery_backend}")
    print(f"Concurrency: {settings.CELERY_WORKER_CONCURRENCY}")
    print(f"Tasks: {celery_app.conf['task_queues']}")