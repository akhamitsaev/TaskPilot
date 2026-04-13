# C4 Component Diagram: TaskPilot Agent Core

## Overview
Component diagram showing the internal structure of the Celery Worker and FastAPI Gateway.

```mermaid
C4Component
    title Component Diagram - TaskPilot Agent Core

    System_Boundary(worker, "Celery Worker") {
        Container(task_processor, "Task Processor", "Python/Celery", "Обработка сообщений из очереди")
        Container(agent_core, "Agent Core", "Python/Mistral LLM", "Классификация и извлечение сущностей")
        Container(retriever, "Retriever", "Python/FAISS", "Семантический поиск задач")
        Container(repo, "Task Repository", "Python/SQLAlchemy", "Repository pattern для CRUD")
        Container(cb, "Circuit Breaker", "Python/Redis", "Защита от каскадных сбоев")
    }

    System_Boundary(api, "FastAPI Gateway") {
        Container(auth, "Auth Module", "Python/JWT", "JWT authentication, token refresh")
        Container(rate_limiter, "Rate Limiter", "Python/slowapi", "Rate limiting per user/IP")
        Container(chat_route, "Chat Route", "Python/FastAPI", "POST /chat endpoint")
        Container(tasks_route, "Tasks Route", "Python/FastAPI", "GET /tasks endpoint")
        Container(metrics_middleware, "Metrics Middleware", "Python/Prometheus", "Сбор HTTP метрик")
    }

    System_Boundary(db, "PostgreSQL") {
        ContainerDb(tasks_table, "tasks", "Table", "Карточки задач")
        ContainerDb(users_table, "users", "Table", "Пользователи")
        ContainerDb(messages_table, "messages", "Table", "История чата")
        ContainerDb(deps_table, "dependencies", "Table", "Журнал зависимостей")
        ContainerDb(audit_table, "audit_log", "Table", "Журнал аудита")
    }

    Rel(task_processor, agent_core, "Вызывает для анализа")
    Rel(agent_core, retriever, "Запрос кандидатов")
    Rel(retriever, repo, "Проверка доступа")
    Rel(repo, tasks_table, "CRUD операции")
    Rel(repo, users_table, "Проверка прав")
    Rel(repo, messages_table, "Сохранение истории")
    Rel(repo, deps_table, "Запись зависимостей")
    Rel(repo, audit_table, "Аудит действий")
    Rel(task_processor, cb, "Проверка состояния")
    Rel(cb, redis, "Чтение состояния", "Redis")
    
    Rel(chat_route, rate_limiter, "Проверка лимита")
    Rel(rate_limiter, auth, "Валидация токена")
    Rel(chat_route, task_processor, "Push в очередь", "Redis")
    Rel(tasks_route, repo, "GET задачи пользователя")
    Rel(metrics_middleware, prometheus, "Экспорт метрик")

    UpdateLayoutConfig($c4ShapeInRow="4", $c4BoundaryInRow="1")
```

## Component Specifications

### Celery Worker Components

| Component | Responsibility | Key Dependencies |
|-----------|---------------|------------------|
| **Task Processor** | Consumes tasks from Redis queue, orchestrates pipeline | Celery, Redis |
| **Agent Core** | LLM-based classification, entity extraction, response generation | Mistral API, prompts |
| **Retriever** | Semantic search via FAISS, candidate ranking | FAISS, embeddings model |
| **Task Repository** | Repository pattern for DB operations, RLS context management | SQLAlchemy, PostgreSQL |
| **Circuit Breaker** | Monitors external service health, prevents cascade failures | Redis, metrics |

### FastAPI Gateway Components

| Component | Responsibility | Key Dependencies |
|-----------|---------------|------------------|
| **Auth Module** | JWT login, token refresh, user validation | bcrypt, JWT library |
| **Rate Limiter** | Per-user/IP rate limiting (60/min chat, 100/min tasks) | slowapi, Redis |
| **Chat Route** | POST /chat endpoint, message validation, queue push | Celery, Pydantic |
| **Tasks Route** | GET /tasks endpoint, user task retrieval | Task Repository |
| **Metrics Middleware** | Collects HTTP request metrics, latency tracking | Prometheus client |

### Database Tables

| Table | Purpose | RLS Policy |
|-------|---------|------------|
| **tasks** | Task cards with status, priority, deadline, problem | `group_id = current_setting('app.current_group_id')` |
| **users** | User accounts with credentials and group assignment | N/A (auth only) |
| **messages** | Chat history for context and audit | `group_id = current_setting('app.current_group_id')` |
| **dependencies** | Task dependency journal | `task_id IN (SELECT id FROM tasks WHERE ...)` |
| **audit_log** | Action audit trail | Admin access only |
