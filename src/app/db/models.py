"""
TaskPilot Database Models
SQLAlchemy ORM модели для PostgreSQL таблиц
"""

from sqlalchemy import Column, String, Text, Integer, Boolean, DateTime, ForeignKey, ARRAY, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.engine import Base
import uuid

# ============================================================================
# Helper
# ============================================================================

def generate_uuid():
    return str(uuid.uuid4())

# ============================================================================
# Models
# ============================================================================

class Group(Base):
    """Группа/отдел пользователей"""
    __tablename__ = "groups"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    users = relationship("User", back_populates="group")
    tasks = relationship("Task", back_populates="group")


class User(Base):
    """Пользователь системы"""
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    group = relationship("Group", back_populates="users")
    tasks = relationship("Task", back_populates="user")


class Task(Base):
    """Задача (основная сущность)"""
    __tablename__ = "tasks"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), default="new")
    priority = Column(Integer, default=5)
    deadline = Column(DateTime(timezone=True), nullable=True)
    problem = Column(Text, nullable=True)
    dependencies = Column(ARRAY(UUID(as_uuid=True)), default=list)
    source_message_id = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="tasks")
    group = relationship("Group", back_populates="tasks")


class Dependency(Base):
    """Журнал зависимостей между задачами"""
    __tablename__ = "dependencies"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    depends_on_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    status = Column(String(50), default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    
    __table_args__ = (
        UniqueConstraint('task_id', 'depends_on_id', name='unique_task_dependency'),
    )


class Message(Base):
    """Сообщения чата (история диалога)"""
    __tablename__ = "messages"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
    content = Column(Text, nullable=False)
    role = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Журнал аудита действий"""
    __tablename__ = "audit_log"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    action = Column(String(255), nullable=False)
    resource_type = Column(String(100))
    resource_id = Column(UUID(as_uuid=True))
    details = Column(JSONB, nullable=True)
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())