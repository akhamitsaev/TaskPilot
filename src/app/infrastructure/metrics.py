"""
TaskPilot Prometheus Metrics + OpenTelemetry
Метрики для мониторинга производительности и LLM
Обновлено: TTFT/TPOT histogram buckets + CPU gauge
"""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import time
import psutil
import structlog
from typing import Optional
from contextlib import contextmanager

logger = structlog.get_logger(__name__)

# ============================================================================
# Prometheus Metrics
# ============================================================================

# HTTP Metrics
HTTP_REQUEST_COUNT = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status_code']
)

HTTP_REQUEST_DURATION = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration',
    ['method', 'endpoint'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# LLM Metrics
LLM_REQUEST_COUNT = Counter(
    'llm_requests_total',
    'Total LLM requests',
    ['model', 'status']
)

LLM_TOKENS_INPUT = Counter(
    'llm_tokens_input_total',
    'Total input tokens to LLM',
    ['model']
)

LLM_TOKENS_OUTPUT = Counter(
    'llm_tokens_output_total',
    'Total output tokens from LLM',
    ['model']
)

# TTFT - Time to First Token (с явными buckets для histogram_quantile)
LLM_TTFT = Histogram(
    'llm_time_to_first_token_seconds',
    'Time to first token from LLM',
    ['model'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]  # ← Явные buckets для p50/p95
)

# TPOT - Time per Output Token (с явными buckets для histogram_quantile)
LLM_TPOT = Histogram(
    'llm_time_per_output_token_seconds',
    'Time per output token from LLM',
    ['model'],
    buckets=[0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0]  # ← Явные buckets для p50/p95
)

LLM_REQUEST_COST = Counter(
    'llm_request_cost_usd',
    'Cost of LLM requests in USD',
    ['model']
)

# Celery Metrics
CELERY_TASK_COUNT = Counter(
    'celery_tasks_total',
    'Total Celery tasks',
    ['task_name', 'status']
)

CELERY_TASK_DURATION = Histogram(
    'celery_task_duration_seconds',
    'Celery task duration',
    ['task_name'],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0]
)

CELERY_QUEUE_LENGTH = Gauge(
    'celery_queue_length',
    'Current Celery queue length'
)

# System Metrics
SYSTEM_CPU_PERCENT = Gauge(
    'system_cpu_percent',
    'Current CPU usage percent'
)

SYSTEM_MEMORY_PERCENT = Gauge(
    'system_memory_percent',
    'Current memory usage percent'
)

# Container CPU Metrics (для cAdvisor интеграции)
CONTAINER_CPU_PERCENT = Gauge(
    'container_cpu_usage_percent',
    'Container CPU usage percent',
    ['container']
)

# Circuit Breaker Metrics
CIRCUIT_BREAKER_STATE = Gauge(
    'circuit_breaker_state',
    'Circuit breaker state (0=CLOSED, 1=OPEN, 2=HALF_OPEN)',
    ['service']
)

# ============================================================================
# OpenTelemetry Setup
# ============================================================================

def setup_opentelemetry():
    """Настройка OpenTelemetry"""
    # Tracing
    trace.set_tracer_provider(TracerProvider())
    tracer_provider = trace.get_tracer_provider()
    
    # Metrics
    reader = PrometheusMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    
    logger.info("opentelemetry_initialized")

# ============================================================================
# LLM Metrics Tracker
# ============================================================================

class LLMMetricsTracker:
    """Трекер метрик LLM запросов с TTFT/TPOT"""
    
    # Стоимость токенов (USD за 1K токенов) - актуально для Mistral
    PRICING = {
        "open-mistral-7b": {"input": 0.00025, "output": 0.00025},
        "mistral-small-latest": {"input": 0.0002, "output": 0.0006},
        "mistral-medium-latest": {"input": 0.0027, "output": 0.0081},
        "mistral-large-latest": {"input": 0.004, "output": 0.012},
    }
    
    def __init__(self, model: str):
        self.model = model
        self.start_time = None
        self.first_token_time = None
        self.input_tokens = 0
        self.output_tokens = 0
    
    def start_request(self):
        """Начало запроса"""
        self.start_time = time.time()
        LLM_REQUEST_COUNT.labels(model=self.model, status="started").inc()
    
    def record_first_token(self):
        """Запись времени до первого токена (TTFT)"""
        if self.start_time and not self.first_token_time:
            self.first_token_time = time.time()
            ttft = self.first_token_time - self.start_time
            LLM_TTFT.labels(model=self.model).observe(ttft)  # ← Запись в histogram
            logger.debug("llm_first_token", ttft_seconds=round(ttft, 3))
    
    def complete_request(self, input_tokens: int, output_tokens: int, status: str = "success"):
        """Завершение запроса с TPOT метрикой"""
        end_time = time.time()
        
        # Токены
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        LLM_TOKENS_INPUT.labels(model=self.model).inc(input_tokens)
        LLM_TOKENS_OUTPUT.labels(model=self.model).inc(output_tokens)
        
        # TPOT - Time per Output Token (если есть первый токен и выходные токены)
        if self.first_token_time and output_tokens > 0:
            generation_time = end_time - self.first_token_time
            tpot = generation_time / output_tokens
            LLM_TPOT.labels(model=self.model).observe(tpot)  # ← Запись в histogram
            logger.debug("llm_generation_complete", tpot_seconds=round(tpot, 4))
        
        # Стоимость
        cost = self._calculate_cost(input_tokens, output_tokens)
        LLM_REQUEST_COST.labels(model=self.model).inc(cost)
        
        # Счётчик
        LLM_REQUEST_COUNT.labels(model=self.model, status=status).inc()
        
        logger.info(
            "llm_request_completed",
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            ttft_ms=round((self.first_token_time - self.start_time) * 1000, 2) if self.first_token_time else None,
            tpot_ms=round(((end_time - self.first_token_time) / max(output_tokens, 1)) * 1000, 2) if self.first_token_time else None,
            total_time_ms=round((end_time - self.start_time) * 1000, 2),
            cost_usd=round(cost, 6),
            status=status
        )
    
    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Расчёт стоимости запроса"""
        pricing = self.PRICING.get(self.model, self.PRICING["mistral-small-latest"])
        return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1000

# ============================================================================
# Middleware for HTTP Metrics
# ============================================================================

class MetricsMiddleware:
    """Middleware для сбора HTTP метрик"""
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        
        start_time = time.time()
        method = scope["method"]
        path = scope["path"]
        
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code = message["status"]
                duration = time.time() - start_time
                
                HTTP_REQUEST_COUNT.labels(
                    method=method,
                    endpoint=path,
                    status_code=status_code
                ).inc()
                
                HTTP_REQUEST_DURATION.labels(
                    method=method,
                    endpoint=path
                ).observe(duration)
            
            return await send(message)
        
        return await self.app(scope, receive, send_wrapper)

# ============================================================================
# System Metrics Collector
# ============================================================================

def collect_system_metrics():
    """Сбор системных метрик"""
    SYSTEM_CPU_PERCENT.set(psutil.cpu_percent(interval=1))
    SYSTEM_MEMORY_PERCENT.set(psutil.virtual_memory().percent)

# ============================================================================
# Metrics Endpoint
# ============================================================================

def get_metrics_response() -> Response:
    """Генерация Prometheus metrics response"""
    collect_system_metrics()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ============================================================================
# Circuit Breaker Metrics Update
# ============================================================================

def update_circuit_breaker_metrics(service: str, state: int | str):
    """
    Обновление метрик Circuit Breaker
    Принимает как int (0,1,2), так и строку ("closed", "open", "half_open")
    """
    # Конвертация строки в число если нужно
    if isinstance(state, str):
        state_map = {"closed": 0, "open": 1, "half_open": 2}
        state = state_map.get(state.lower(), 0)
    
    CIRCUIT_BREAKER_STATE.labels(service=service).set(float(state))

# ============================================================================
# Celery Task Metrics
# ============================================================================

def record_celery_task(task_name: str, status: str, duration: float):
    """Запись метрик Celery задачи"""
    CELERY_TASK_COUNT.labels(task_name=task_name, status=status).inc()
    CELERY_TASK_DURATION.labels(task_name=task_name).observe(duration)

def update_queue_length(length: int):
    """Обновление длины очереди"""
    CELERY_QUEUE_LENGTH.set(length)

# ============================================================================
# Container CPU Metrics (для cAdvisor)
# ============================================================================

def update_container_cpu(container_name: str, cpu_percent: float):
    """Обновление метрик CPU контейнера"""
    CONTAINER_CPU_PERCENT.labels(container=container_name).set(cpu_percent)