"""
TaskPilot Celery Tasks
Фоновые задачи для обработки сообщений
"""

from celery import Task
from app.worker.celery_app import celery_app
from app.worker.agent import analyze_message, generate_task_summary
from app.db.engine import db_session, rls_context
from app.db.task_repository import TaskRepository
from app.config import settings
from app.infrastructure import llm_circuit_breaker
import structlog
import uuid
from datetime import datetime
from typing import Optional

logger = structlog.get_logger(__name__)

# ============================================================================
# Base Task Class (с сессией БД)
# ============================================================================

class DatabaseTask(Task):
    """Базовый класс для задач с доступом к БД"""
    
    _db = None
    
    @property
    def db(self):
        if self._db is None:
            self._db = db_session()
        return self._db
    
    def after_return(self, *args, **kwargs):
        """Очистка сессии после выполнения задачи"""
        if self._db is not None:
            self._db.close()
            self._db = None

# ============================================================================
# Main Task: Process Message
# ============================================================================

@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def process_message(self, user_id: str, group_id: str, message: str):
    """
    Основная задача обработки сообщения пользователя
    
    Pipeline:
    1. Анализ сообщения через LLM Agent
    2. Если задача → создание/обновление в БД
    3. Синхронизация с FAISS
    4. Сохранение истории сообщений
    5. Возврат ответа пользователю
    
    Args:
        user_id: UUID пользователя
        group_id: UUID группы
        message: Текст сообщения
    """
    task_id = self.request.id
    logger.info("process_message_started", task_id=task_id, user_id=user_id)
    
    try:
        # 1. Установка RLS контекста
        repo = TaskRepository(self.db)
        repo.set_rls_context(user_id, group_id)
        
        # 2. Получение контекста (последние сообщения)
        recent_messages = repo.get_recent_messages(uuid.UUID(user_id), limit=5)
        context = [{"role": m.role, "content": m.content} for m in recent_messages]
        
        # 3. Анализ сообщения через Agent
        agent_response = analyze_message(message, context=context)
        logger.info(
            "agent_analysis_completed",
            is_task=agent_response.is_task,
            confidence=agent_response.confidence
        )
        
        # 4. Сохранение сообщения пользователя в историю
        repo.save_message(uuid.UUID(user_id), uuid.UUID(group_id), message, "user")
        
        # 5. Обработка результата
        if agent_response.is_task and agent_response.task:
            # Создание задачи в БД
            task = repo.create_task(
                user_id=uuid.UUID(user_id),
                group_id=uuid.UUID(group_id),
                title=agent_response.task.title,
                description=agent_response.task.description,
                deadline=parse_deadline(agent_response.task.deadline),
                priority=agent_response.task.priority,
                problem=agent_response.task.problem,
                source_message_id=str(task_id)
            )
            
            logger.info("task_created", task_id=str(task.id), title=task.title)
            
            # Сохранение ответа агента в историю
            repo.save_message(
                uuid.UUID(user_id),
                uuid.UUID(group_id),
                agent_response.response_text,
                "assistant"
            )
            
            return {
                "success": True,
                "is_task": True,
                "task_id": str(task.id),
                "task_title": task.title,
                "response": agent_response.response_text
            }
        
        else:
            # Не задача — просто ответ пользователя
            repo.save_message(
                uuid.UUID(user_id),
                uuid.UUID(group_id),
                agent_response.response_text,
                "assistant"
            )
            
            return {
                "success": True,
                "is_task": False,
                "response": agent_response.response_text
            }
    
    except Exception as e:
        logger.error("process_message_failed", error=str(e))
        
        # Retry логика
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30)
        
        # Fallback ответ
        return {
            "success": False,
            "error": str(e),
            "response": "Произошла ошибка при обработке сообщения. Попробуйте ещё раз."
        }
    
    finally:
        # Очистка RLS контекста
        try:
            repo.clear_rls_context()
        except:
            pass

# ============================================================================
# Helper Functions
# ============================================================================

def parse_deadline(deadline_str: Optional[str]) -> Optional[datetime]:
    """Парсинг дедлайна из строки в datetime"""
    if not deadline_str:
        return None
    
    try:
        # Попытка парсинга ISO 8601
        return datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
    except:
        return None

# ============================================================================
# Additional Tasks
# ============================================================================

@celery_app.task(base=DatabaseTask, bind=True)
def get_task_summary(self, user_id: str, group_id: str, task_id: str):
    """Получение сводки по задаче"""
    try:
        repo = TaskRepository(self.db)
        repo.set_rls_context(user_id, group_id)
        
        task = repo.get_task(uuid.UUID(task_id))
        if not task:
            return {"success": False, "error": "Task not found"}
        
        # Генерация сводки через LLM
        summary = generate_task_summary(
            {"title": task.title, "status": task.status, "deadline": task.deadline, "problem": task.problem},
            []
        )
        
        return {
            "success": True,
            "task_id": task_id,
            "title": task.title,
            "status": task.status,
            "summary": summary
        }
    
    except Exception as e:
        logger.error("get_task_summary_failed", error=str(e))
        return {"success": False, "error": str(e)}