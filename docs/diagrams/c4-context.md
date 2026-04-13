# C4 Context Diagram: TaskPilot Agent

## Overview
System context diagram showing TaskPilot Agent and its relationships with users and external systems.

```mermaid
C4Context
    title System Context Diagram - TaskPilot Agent

    Person(user, "Пользователь", "Руководитель проекта, тимлид, специалист")
    
    System_Boundary(taskpilot, "TaskPilot Agent") {
        Container(ui, "Streamlit UI", "Python", "Чат-интерфейс и бэклог задач")
        Container(api, "FastAPI Gateway", "Python", "API gateway, auth, rate limiting")
        Container(worker, "Celery Worker", "Python", "Асинхронная обработка задач")
        ContainerDb(db, "PostgreSQL", "PostgreSQL 15", "Хранение задач, пользователей, RLS политики")
        ContainerDb(redis, "Redis", "Redis 7", "Очередь задач, кэш, circuit breaker state")
        ContainerDb(faiss, "FAISS Index", "Python/FAISS", "Векторный поиск задач (384d embeddings)")
    }

    System_Ext(mistral, "Mistral AI API", "External LLM", "Генерация и анализ текста")
    System_Ext(prometheus, "Prometheus", "Monitoring", "Сбор метрик и алерты")
    System_Ext(grafana, "Grafana", "Visualization", "Дашборды и визуализация метрик")

    Rel(user, ui, "Использует чат-интерфейс", "HTTPS")
    Rel(ui, api, "Отправляет сообщения", "REST API")
    Rel(api, redis, "Публикует задачи в очередь", "Redis Queue")
    Rel(redis, worker, "Потребляет задачи", "Celery")
    Rel(worker, mistral, "Анализ через LLM", "HTTPS API")
    Rel(worker, db, "CRUD операции", "SQLAlchemy + RLS")
    Rel(worker, faiss, "Семантический поиск", "FAISS Index")
    Rel(worker, prometheus, "Экспорт метрик", "HTTP /metrics")
    Rel(prometheus, grafana, "Визуализация", "PromQL")

    UpdateRelStyle(user, ui, $offsetY="-40")
    UpdateRelStyle(ui, api, $offsetX="40")
    UpdateRelStyle(api, redis, $offsetY="-30")
    UpdateRelStyle(redis, worker, $offsetX="-40")
    UpdateRelStyle(worker, mistral, $offsetY="40")
    UpdateRelStyle(worker, db, $offsetX="60")
    UpdateRelStyle(worker, faiss, $offsetX="-60")
```

## Boundaries

| Boundary | Description |
|----------|-------------|
| **TaskPilot Agent** | Основная система управления задачами с агентной обработкой |
| **User** | Внешний пользователь (руководитель, тимлид, специалист) |
| **Mistral AI API** | Внешний LLM-сервис для анализа и генерации текста |
| **Prometheus/Grafana** | Система мониторинга и визуализации |

## Trust Boundaries

- **User ↔ UI**: HTTPS, JWT-аутентификация
- **UI ↔ API**: Внутренняя сеть Docker, CORS policies
- **API ↔ Worker**: Redis queue, изоляция через сеть
- **Worker ↔ LLM**: HTTPS с API key authentication
- **Worker ↔ DB**: PostgreSQL RLS для изоляции данных
