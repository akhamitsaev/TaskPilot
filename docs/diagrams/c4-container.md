# C4 Container Diagram: TaskPilot Agent

## Overview
Container diagram showing the high-level technology choices and how containers communicate.

```mermaid
C4Container
    title Container Diagram - TaskPilot Agent

    Person(user, "Пользователь", "Руководитель проекта, тимлид")

    System_Boundary(taskpilot, "TaskPilot Agent") {
        Container(ui, "Streamlit UI", "Python 3.10, Streamlit", "Чат-интерфейс и бэклог задач", "HTTPS :8501")
        Container(api, "FastAPI Gateway", "Python 3.10, FastAPI", "API gateway, auth, rate limiting", "HTTP :8000")
        Container(worker, "Celery Worker", "Python 3.10, Celery", "Асинхронная обработка задач", "N/A")
        
        ContainerDb(db, "PostgreSQL", "PostgreSQL 15", "Хранение задач, пользователей, RLS политики", "TCP :5432")
        ContainerDb(redis, "Redis", "Redis 7", "Очередь задач, кэш, circuit breaker state", "TCP :6379")
        ContainerDb(faiss, "FAISS Index", "Python/FAISS, NumPy", "Векторный поиск задач (384d)", "In-Memory")
        
        Container(prometheus, "Prometheus", "Prometheus", "Сбор метрик", "HTTP :9090")
        Container(grafana, "Grafana", "Grafana", "Визуализация метрик", "HTTP :3000")
    }

    System_Ext(mistral, "Mistral AI API", "External LLM", "Генерация и анализ текста")

    Rel(user, ui, "Использует чат", "HTTPS")
    Rel(ui, api, "REST API вызовы", "JSON/HTTP")
    Rel(api, redis, "Push tasks", "Redis Queue")
    Rel(redis, worker, "Consume tasks", "Celery Protocol")
    Rel(worker, db, "CRUD + RLS", "SQLAlchemy Async")
    Rel(worker, faiss, "Add/Search vectors", "FAISS API")
    Rel(worker, mistral, "LLM requests", "HTTPS API")
    Rel(api, prometheus, "Expose /metrics", "HTTP")
    Rel(worker, prometheus, "Expose /metrics", "HTTP")
    Rel(prometheus, grafana, "Query metrics", "PromQL")

    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

## Container Specifications

| Container | Technology | Responsibilities | Ports |
|-----------|------------|------------------|-------|
| **Streamlit UI** | Python 3.10, Streamlit | Чат-интерфейс, отображение бэклога, JWT auth flow | :8501 |
| **FastAPI Gateway** | Python 3.10, FastAPI | Auth, rate limiting, validation, push to queue, health checks | :8000 |
| **Celery Worker** | Python 3.10, Celery | Async message processing, LLM calls, DB writes, FAISS sync | N/A |
| **PostgreSQL** | PostgreSQL 15 | Task storage, user management, RLS policies, audit log | :5432 |
| **Redis** | Redis 7 | Task queue, caching, circuit breaker state, rate limiting | :6379 |
| **FAISS Index** | Python/FAISS, NumPy | Semantic search, vector embeddings (384d), persistence to disk | In-Memory |
| **Prometheus** | Prometheus | Metrics collection, alerting rules, time-series storage | :9090 |
| **Grafana** | Grafana | Dashboards, visualization, alerts | :3000 |

## Communication Protocols

| Connection | Protocol | Data Format | Security |
|------------|----------|-------------|----------|
| User ↔ UI | HTTPS | JSON | JWT |
| UI ↔ API | HTTP | JSON | CORS |
| API ↔ Redis | Redis Protocol | Pickle/JSON | Network isolation |
| Worker ↔ PostgreSQL | PostgreSQL Wire | SQL (SQLAlchemy ORM) | RLS, credentials |
| Worker ↔ Mistral | HTTPS | JSON | API Key |
| Prometheus ↔ Containers | HTTP | Prometheus Text | Network isolation |
