"""
TaskPilot Task Repository
CRUD операции для задач с синхронизацией FAISS
"""

from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime

from app.db.models import Task, User, Group, Message, Dependency
from app.search.faiss_index import faiss_index

class TaskRepository:
    """Репозиторий для работы с задачами"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def set_rls_context(self, user_id: str, group_id: str):
        """Установка RLS контекста для сессии"""
        self.db.execute(
            text("SELECT set_app_context(:user_id, :group_id)"),
            {"user_id": user_id, "group_id": group_id}
        )
        self.db.commit()
    
    def clear_rls_context(self):
        """Очистка RLS контекста"""
        self.db.execute(text("SELECT clear_app_context()"))
        self.db.commit()
    
    def create_task(
        self,
        user_id: uuid.UUID,
        group_id: uuid.UUID,
        title: str,
        description: str = "",
        deadline: Optional[datetime] = None,
        priority: int = 5,
        problem: Optional[str] = None,
        source_message_id: Optional[str] = None
    ) -> Task:
        """Создание задачи + синхронизация с FAISS"""
        
        task = Task(
            user_id=user_id,
            group_id=group_id,
            title=title,
            description=description,
            deadline=deadline,
            priority=priority,
            problem=problem,
            source_message_id=source_message_id,
            status="new"
        )
        
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        
        # Синхронизация с FAISS
        try:
            faiss_index.add_task(str(task.id), title, description)
        except Exception as e:
            # FAISS ошибка не должна ломать создание задачи
            pass
        
        return task
    
    def get_task(self, task_id: uuid.UUID) -> Optional[Task]:
        """Получение задачи по ID"""
        return self.db.query(Task).filter(Task.id == task_id).first()
    
    def search_tasks(self, query: str, group_id: uuid.UUID, k: int = 5) -> List[Task]:
        """Семантический поиск задач через FAISS"""
        
        # 1. Поиск в FAISS
        faiss_results = faiss_index.search(query, k=k)
        
        if not faiss_results:
            return []
        
        # 2. Получение полных данных из PostgreSQL
        task_ids = [uuid.UUID(task_id) for task_id, _ in faiss_results]
        tasks = self.db.query(Task).filter(
            Task.id.in_(task_ids),
            Task.group_id == group_id
        ).all()
        
        return tasks
    
    def update_task_status(self, task_id: uuid.UUID, status: str) -> Optional[Task]:
        """Обновление статуса задачи"""
        task = self.get_task(task_id)
        if task:
            task.status = status
            task.updated_at = datetime.now()
            self.db.commit()
            self.db.refresh(task)
        return task
    
    def update_task_problem(self, task_id: uuid.UUID, problem: str) -> Optional[Task]:
        """Обновление проблемы задачи"""
        task = self.get_task(task_id)
        if task:
            task.problem = problem
            task.updated_at = datetime.now()
            self.db.commit()
            self.db.refresh(task)
        return task
    
    def get_user_tasks(self, user_id: uuid.UUID, group_id: uuid.UUID, limit: int = 20) -> List[Task]:
        """Получение задач пользователя"""
        return self.db.query(Task).filter(
            Task.user_id == user_id,
            Task.group_id == group_id
        ).order_by(Task.created_at.desc()).limit(limit).all()
    
    def save_message(
        self,
        user_id: uuid.UUID,
        group_id: uuid.UUID,
        content: str,
        role: str
    ) -> Message:
        """Сохранение сообщения в историю"""
        message = Message(
            user_id=user_id,
            group_id=group_id,
            content=content,
            role=role
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message
    
    def get_recent_messages(self, user_id: uuid.UUID, limit: int = 10) -> List[Message]:
        """Получение последних сообщений для контекста"""
        return self.db.query(Message).filter(
            Message.user_id == user_id
        ).order_by(Message.created_at.desc()).limit(limit).all()