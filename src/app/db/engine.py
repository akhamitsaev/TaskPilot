"""
TaskPilot Database Engine
Подключение к PostgreSQL с пулом соединений
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from app.config import settings
import structlog

logger = structlog.get_logger(__name__)

# ============================================================================
# Database Engine
# ============================================================================

engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    pool_recycle=settings.DB_POOL_RECYCLE,
    echo=settings.DEBUG  # SQL логи в debug режиме
)

# ============================================================================
# Session Factory
# ============================================================================

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Scoped session для thread-safety (важно для Celery)
db_session = scoped_session(SessionLocal)

# ============================================================================
# Dependency (для FastAPI)
# ============================================================================

def get_db():
    """
    Dependency для получения сессии БД в FastAPI
    
    Usage:
        @app.post("/tasks")
        def create_task(db: Session = Depends(get_db)):
            ...
    """
    db = db_session()
    try:
        yield db
    finally:
        db.close()

# ============================================================================
# RLS Context Manager
# ============================================================================

from contextlib import contextmanager

@contextmanager
def rls_context(user_id: str, group_id: str):
    """
    Контекстный менеджер для установки RLS контекста
    
    Usage:
        with rls_context(str(user_id), str(group_id)):
            # Все запросы в этом блоке будут с RLS
            db.query(Task).filter(...)
    """
    try:
        # Устанавливаем контекст через PostgreSQL функцию
        db = db_session()
        db.execute(
            text("SELECT set_app_context(:user_id, :group_id)"),
            {"user_id": user_id, "group_id": group_id}
        )
        db.commit()
        yield db
    finally:
        # Очищаем контекст
        db.execute(text("SELECT clear_app_context()"))
        db.commit()
        db.close()

# ============================================================================
# Base Model
# ============================================================================

from sqlalchemy.orm import declarative_base

Base = declarative_base()

# ============================================================================
# Initialization
# ============================================================================

def init_db():
    """Инициализация БД (создание таблиц)"""
    logger.info("db_initialization_started")
    Base.metadata.create_all(bind=engine)
    logger.info("db_initialization_completed")

# ============================================================================
# Main (для тестирования)
# ============================================================================

if __name__ == "__main__":
    print("=== TaskPilot Database Connection Test ===")
    try:
        conn = engine.connect()
        print("✅ Database connection successful")
        conn.close()
    except Exception as e:
        print(f"❌ Database connection failed: {e}")