"""
TaskPilot Configuration Module
Централизованное управление настройками приложения
"""

import os
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional, List

class Settings(BaseSettings):
    """Конфигурация приложения TaskPilot"""
    
    # ========================================================================
    # Application Info
    # ========================================================================
    APP_NAME: str = "TaskPilot"
    APP_VERSION: str = "0.2.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # ========================================================================
    # Database (PostgreSQL) - Docker hostnames
    # ========================================================================
    DB_USER: str = "taskpilot"
    DB_PASSWORD: str = "taskpilot_secret_123"
    DB_HOST: str = "postgres"  # Имя сервиса в docker-compose.yml
    DB_PORT: int = 5432
    DB_NAME: str = "taskpilot"
    
    DATABASE_URL: Optional[str] = None
    
    @property
    def db_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_PRE_PING: bool = True
    DB_POOL_RECYCLE: int = 3600

    # ========================================================================
    # Redis - Docker hostname
    # ========================================================================
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_URL: Optional[str] = None

    @property
    def redis_url(self) -> str:
        if self.REDIS_URL:
            return self.REDIS_URL
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ========================================================================
    # Celery Settings - КРИТИЧНО ДЛЯ ЗАПУСКА
    # ========================================================================
    CELERY_BROKER_URL: Optional[str] = None
    CELERY_RESULT_BACKEND: Optional[str] = None
    CELERY_WORKER_CONCURRENCY: int = 2
    CELERY_TASK_ACKS_LATE: bool = True              # ← ДОБАВЛЕНО
    CELERY_TASK_REJECT_ON_WORKER_LOST: bool = True  # ← ДОБАВЛЕНО

    @property
    def celery_broker(self) -> str:
        return self.CELERY_BROKER_URL or self.redis_url

    @property
    def celery_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.redis_url

    # ========================================================================
    # Security
    # ========================================================================
    JWT_SECRET: str = "supersecret32bytesrandomkey12345678"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY: int = 1800
    BCRYPT_ROUNDS: int = 12

    # ========================================================================
    # LLM (Mistral)
    # ========================================================================
    MISTRAL_API_KEY: str = ""
    MISTRAL_MODEL: str = "mistral-small-latest"
    LLM_TIMEOUT: int = 30
    LLM_MAX_TOKENS: int = 2000
    LLM_TEMPERATURE: float = 0.3

    # ========================================================================
    # FAISS
    # ========================================================================
    FAISS_INDEX_PATH: str = "./data/faiss_index"
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIMENSION: int = 384
    FAISS_TOP_K: int = 5
    FAISS_THRESHOLD: float = 0.75

    # ========================================================================
    # App URLs
    # ========================================================================
    API_URL: str = "http://localhost:8000"
    UI_URL: str = "http://localhost:8501"
    ALLOWED_ORIGINS: List[str] = ["*"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"

    def validate_settings(self) -> bool:
        errors = []
        if not self.MISTRAL_API_KEY:
            print("⚠️ WARNING: MISTRAL_API_KEY is empty!")
        if len(self.JWT_SECRET) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters!")
        return True

@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()