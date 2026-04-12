"""
TaskPilot Circuit Breaker
Защита от каскадных сбоев при недоступности внешних сервисов (LLM API, БД, Redis)

Реализация паттерна Circuit Breaker:
- CLOSED: Нормальная работа, запросы проходят
- OPEN: Сбой, запросы блокируются, таймер обратного отсчёта
- HALF-OPEN: Тестовый запрос, проверка восстановления
"""

import time
import threading
from enum import Enum
from typing import Optional, Callable, Any
from datetime import datetime, timedelta
import redis
import structlog
from app.config import settings

logger = structlog.get_logger(__name__)

# ============================================================================
# Circuit Breaker States
# ============================================================================

class CircuitState(Enum):
    """Состояния Circuit Breaker"""
    CLOSED = "closed"       # Нормальная работа
    OPEN = "open"           # Сбой, запросы блокируются
    HALF_OPEN = "half_open" # Тестовый режим восстановления


# ============================================================================
# Circuit Breaker Configuration
# ============================================================================

class CircuitBreakerConfig:
    """Конфигурация Circuit Breaker"""
    
    def __init__(
        self,
        failure_threshold: int = 5,      # Количество ошибок для открытия
        recovery_timeout: int = 60,      # Время до попытки восстановления (сек)
        half_open_max_calls: int = 1,    # Количество тестовых запросов в HALF_OPEN
        timeout_window: int = 300        # Окно времени для подсчёта ошибок (сек)
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.timeout_window = timeout_window


# ============================================================================
# Circuit Breaker Class
# ============================================================================

class CircuitBreaker:
    """
    Circuit Breaker для защиты от сбоев внешних сервисов
    
    Использует Redis для распределённого состояния (между воркерами).
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
        redis_client: Optional[redis.Redis] = None
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.redis = redis_client
        
        # Ключи в Redis для хранения состояния
        self._state_key = f"circuit:{name}:state"
        self._failures_key = f"circuit:{name}:failures"
        self._last_failure_key = f"circuit:{name}:last_failure"
        self._opened_at_key = f"circuit:{name}:opened_at"
        self._half_open_calls_key = f"circuit:{name}:half_open_calls"
        
        # Локальный lock для thread-safety
        self._lock = threading.Lock()
        
        logger.info("circuit_breaker_init", name=name, config=self._get_config_dict())
    
    def _get_config_dict(self) -> dict:
        """Конфигурация для логирования"""
        return {
            "failure_threshold": self.config.failure_threshold,
            "recovery_timeout": self.config.recovery_timeout,
            "half_open_max_calls": self.config.half_open_max_calls,
            "timeout_window": self.config.timeout_window
        }
    
    def _get_state(self) -> CircuitState:
        """Получение текущего состояния из Redis"""
        if self.redis:
            try:
                state = self.redis.get(self._state_key)
                if state:
                    return CircuitState(state.decode())
            except Exception as e:
                logger.error("circuit_redis_error", error=str(e))
        
        # По умолчанию CLOSED
        return CircuitState.CLOSED
    
    def _set_state(self, state: CircuitState) -> None:
        """Установка состояния в Redis"""
        if self.redis:
            try:
                self.redis.set(self._state_key, state.value)
                
                # Если OPEN — записываем время открытия
                if state == CircuitState.OPEN:
                    self.redis.set(self._opened_at_key, str(time.time()))
                
                # Если HALF_OPEN — сбрасываем счётчик тестовых вызовов
                if state == CircuitState.HALF_OPEN:
                    self.redis.set(self._half_open_calls_key, "0")
                    
            except Exception as e:
                logger.error("circuit_redis_error", error=str(e))
    
    def _get_failure_count(self) -> int:
        """Получение количества ошибок в окне времени"""
        if self.redis:
            try:
                # Получаем все timestamp ошибок в окне
                now = time.time()
                window_start = now - self.config.timeout_window
                
                # Удаляем старые ошибки
                self.redis.zremrangebyscore(self._failures_key, 0, window_start)
                
                # Считаем текущие
                return self.redis.zcard(self._failures_key)
            except Exception as e:
                logger.error("circuit_redis_error", error=str(e))
        
        return 0
    
    def _record_failure(self) -> None:
        """Запись ошибки в Redis"""
        if self.redis:
            try:
                now = time.time()
                # Добавляем timestamp ошибки
                self.redis.zadd(self._failures_key, {str(now): now})
                # Устанавливаем TTL на ключ (окно + буфер)
                self.redis.expire(self._failures_key, self.config.timeout_window + 60)
                # Запоминаем время последней ошибки
                self.redis.set(self._last_failure_key, str(now))
            except Exception as e:
                logger.error("circuit_redis_error", error=str(e))
    
    def _reset_failures(self) -> None:
        """Сброс счётчика ошибок"""
        if self.redis:
            try:
                self.redis.delete(self._failures_key)
                self.redis.delete(self._last_failure_key)
            except Exception as e:
                logger.error("circuit_redis_error", error=str(e))
    
    def _increment_half_open_calls(self) -> int:
        """Увеличение счётчика тестовых вызовов в HALF_OPEN"""
        if self.redis:
            try:
                return int(self.redis.incr(self._half_open_calls_key))
            except Exception as e:
                logger.error("circuit_redis_error", error=str(e))
        
        return 1
    
    def get_state(self) -> CircuitState:
        """
        Получение текущего состояния с проверкой таймаутов
        
        Returns:
            CircuitState: Текущее состояние
        """
        state = self._get_state()
        
        # Если OPEN — проверяем не истёк ли timeout восстановления
        if state == CircuitState.OPEN:
            if self.redis:
                try:
                    opened_at = self.redis.get(self._opened_at_key)
                    if opened_at:
                        elapsed = time.time() - float(opened_at.decode())
                        if elapsed >= self.config.recovery_timeout:
                            logger.info(
                                "circuit_breaker_transition",
                                name=self.name,
                                from_state="OPEN",
                                to_state="HALF_OPEN",
                                elapsed_seconds=round(elapsed, 2)
                            )
                            self._set_state(CircuitState.HALF_OPEN)
                            return CircuitState.HALF_OPEN
                except Exception as e:
                    logger.error("circuit_redis_error", error=str(e))
        
        return state
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Выполнение функции с защитой Circuit Breaker
        
        Args:
            func: Функция для вызова (например, запрос к LLM API)
            *args, **kwargs: Аргументы функции
        
        Returns:
            Результат выполнения функции
        
        Raises:
            CircuitBreakerOpenError: Если цепь разомкнута
        """
        state = self.get_state()
        
        # OPEN — блокируем запрос
        if state == CircuitState.OPEN:
            logger.warning(
                "circuit_breaker_open",
                name=self.name,
                action="request_blocked"
            )
            raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is OPEN")
        
        # HALF_OPEN — разрешаем тестовый запрос
        if state == CircuitState.HALF_OPEN:
            calls = self._increment_half_open_calls()
            if calls > self.config.half_open_max_calls:
                logger.warning(
                    "circuit_breaker_half_open_limit",
                    name=self.name,
                    max_calls=self.config.half_open_max_calls
                )
                raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' HALF_OPEN limit reached")
        
        # Выполняем функцию
        try:
            result = func(*args, **kwargs)
            
            # Успех — сбрасываем ошибки
            self._on_success()
            
            return result
            
        except Exception as e:
            # Ошибка — записываем сбой
            self._on_failure()
            raise
    
    def _on_success(self) -> None:
        """Обработка успешного выполнения"""
        state = self.get_state()
        
        if state == CircuitState.HALF_OPEN:
            # Успешный тестовый запрос — закрываем цепь
            logger.info(
                "circuit_breaker_transition",
                name=self.name,
                from_state="HALF_OPEN",
                to_state="CLOSED"
            )
            self._set_state(CircuitState.CLOSED)
            self._reset_failures()
        
        elif state == CircuitState.CLOSED:
            # Успех в нормальном режиме — сбрасываем ошибки
            self._reset_failures()
    
    def _on_failure(self) -> None:
        """Обработка сбоя"""
        self._record_failure()
        
        state = self.get_state()
        failure_count = self._get_failure_count()
        
        logger.warning(
            "circuit_breaker_failure",
            name=self.name,
            failure_count=failure_count,
            threshold=self.config.failure_threshold
        )
        
        # Если CLOSED и превышен порог — открываем цепь
        if state == CircuitState.CLOSED and failure_count >= self.config.failure_threshold:
            logger.critical(
                "circuit_breaker_transition",
                name=self.name,
                from_state="CLOSED",
                to_state="OPEN",
                failure_count=failure_count
            )
            self._set_state(CircuitState.OPEN)
        
        # Если HALF_OPEN и ошибка — снова открываем
        elif state == CircuitState.HALF_OPEN:
            logger.warning(
                "circuit_breaker_transition",
                name=self.name,
                from_state="HALF_OPEN",
                to_state="OPEN",
                reason="test_request_failed"
            )
            self._set_state(CircuitState.OPEN)
    
    def get_stats(self) -> dict:
        """Получение статистики Circuit Breaker"""
        return {
            "name": self.name,
            "state": self.get_state().value,
            "failure_count": self._get_failure_count(),
            "failure_threshold": self.config.failure_threshold,
            "recovery_timeout": self.config.recovery_timeout,
            "config": self._get_config_dict()
        }


# ============================================================================
# Custom Exception
# ============================================================================

class CircuitBreakerOpenError(Exception):
    """Исключение при разомкнутой цепи"""
    pass


# ============================================================================
# Factory Function (для удобства создания)
# ============================================================================

def create_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: int = 60,
    redis_url: Optional[str] = None
) -> CircuitBreaker:
    """
    Фабричная функция для создания Circuit Breaker
    
    Args:
        name: Имя сервиса (llm, postgres, redis, faiss)
        failure_threshold: Порог ошибок для открытия
        recovery_timeout: Время до восстановления (сек)
        redis_url: URL Redis для распределённого состояния
    
    Returns:
        CircuitBreaker: Настроенный экземпляр
    """
    config = CircuitBreakerConfig(
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout
    )
    
    redis_client = None
    if redis_url:
        try:
            redis_client = redis.from_url(redis_url)
            redis_client.ping()
        except Exception as e:
            logger.warning("circuit_breaker_redis_unavailable", error=str(e))
    
    return CircuitBreaker(name=name, config=config, redis_client=redis_client)


# ============================================================================
# Global Instances (для основных сервисов)
# ============================================================================

# Circuit Breaker для LLM API (основной)
llm_circuit_breaker = create_circuit_breaker(
    name="llm",
    failure_threshold=5,
    recovery_timeout=60,
    redis_url=settings.REDIS_URL
)

# Circuit Breaker для PostgreSQL
postgres_circuit_breaker = create_circuit_breaker(
    name="postgres",
    failure_threshold=3,
    recovery_timeout=30,
    redis_url=settings.REDIS_URL
)

# Circuit Breaker для Redis
redis_circuit_breaker = create_circuit_breaker(
    name="redis",
    failure_threshold=3,
    recovery_timeout=30,
    redis_url=settings.REDIS_URL  # Fallback на локальный redis если основной упал
)

# Circuit Breaker для FAISS
faiss_circuit_breaker = create_circuit_breaker(
    name="faiss",
    failure_threshold=5,
    recovery_timeout=60,
    redis_url=settings.REDIS_URL
)


# ============================================================================
# Decorator (для удобного применения)
# ============================================================================

def circuit_breaker_protected(cb: CircuitBreaker):
    """
    Декоратор для защиты функции Circuit Breaker
    
    Usage:
        @circuit_breaker_protected(llm_circuit_breaker)
        def call_llm_api():
            ...
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            return cb.call(func, *args, **kwargs)
        wrapper.__name__ = func.__name__
        return wrapper
    return decorator