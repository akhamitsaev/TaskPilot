# Workflow Diagram: Message Processing Flow

## Overview
Detailed workflow showing how a user message is processed through the system, including error branches and fallbacks.

```mermaid
flowchart TD
    Start([Пользователь отправляет сообщение]) --> Auth{Auth Check}
    
    Auth -- Invalid Token --> AuthError[401 Unauthorized]
    AuthError --> End([Конец])
    
    Auth -- Valid --> RateLimit{Rate Limit Check}
    RateLimit -- Exceeded --> RateLimitError[429 Too Many Requests]
    RateLimitError --> End
    
    RateLimit -- OK --> Queue[Push to Redis Queue]
    Queue --> Worker[Celery Worker Pickup]
    
    Worker --> CB{Circuit Breaker}
    CB -- OPEN --> CBFallback[Return Fallback Response]
    CBFallback --> LogError[Log Circuit Breaker State]
    LogError --> End
    
    CB -- CLOSED/HALF_OPEN --> LLM[LLM Analysis]
    
    LLM -- Error/Timeout --> Retry{Retry Count < 3?}
    Retry -- Yes --> RetryQueue[Re-queue with Delay]
    RetryQueue --> Worker
    Retry -- No --> LLMEscape[LLM Escape Hatch]
    LLMEscape --> LogError
    
    LLM -- Success --> Classify{Is Task?}
    
    Classify -- No (Chat) --> SaveMsg[Save to Messages]
    SaveMsg --> GenResponse[Generate Response]
    GenResponse --> ReturnResp[Return to User]
    ReturnResp --> End
    
    Classify -- Yes (Task) --> Search[FAISS Semantic Search]
    Search --> Rank[LLM Reranking]
    Rank --> Threshold{Similarity >= 0.75?}
    
    Threshold -- No --> Clarify[Request Clarification]
    Clarify --> SaveMsg
    Clarify --> ReturnResp
    
    Threshold -- Yes --> Match{Task Found?}
    
    Match -- No --> CreateTask[Create New Task]
    CreateTask --> DepCheck{Dependencies Valid?}
    
    Match -- Yes --> UpdateTask[Update Existing Task]
    UpdateTask --> DepCheck
    
    DepCheck -- Invalid --> RejectTask[Reject + Notify User]
    RejectTask --> LogAudit[Log to Audit]
    LogAudit --> End
    
    DepCheck -- Valid --> AccessCheck{Access Check (RLS)}
    
    AccessCheck -- Denied --> AccessError[403 Forbidden]
    AccessError --> LogAudit
    AccessError --> End
    
    AccessCheck -- Allowed --> DBWrite[PostgreSQL Write]
    DBWrite --> FAISSSync[Sync FAISS Index]
    FAISSSync --> SaveHistory[Save Chat History]
    SaveHistory --> Notify[WebSocket Notify UI]
    Notify --> ReturnTask[Return Task ID + Summary]
    ReturnTask --> End
```

## Step Descriptions

| Step | Component | Description | Timeout |
|------|-----------|-------------|---------|
| **Auth Check** | FastAPI + JWT | Validate access token, extract user_id/group_id | <10ms |
| **Rate Limit Check** | slowapi + Redis | Check per-user rate limits (60/min chat) | <5ms |
| **Push to Queue** | Celery + Redis | Serialize task, push to Redis queue | <10ms |
| **Circuit Breaker** | CircuitBreaker | Check LLM service health state | <5ms |
| **LLM Analysis** | Mistral API | Classify message, extract entities | 2-10s |
| **FAISS Search** | FAISS Index | Top-5 semantic search, 384d vectors | <100ms |
| **LLM Reranking** | Mistral API | Select best match from candidates | 1-3s |
| **Access Check** | PostgreSQL RLS | Verify user has access to task/group | <10ms |
| **DB Write** | SQLAlchemy | Transactional write with RLS context | <50ms |
| **FAISS Sync** | FAISSIndexManager | Add/update vector embedding, persist to disk | <100ms |

## Error Handling & Fallbacks

| Error Type | Detection | Fallback Strategy |
|------------|-----------|-------------------|
| **LLM Timeout** | HTTP timeout >30s | Retry up to 3x with exponential backoff |
| **Circuit Breaker OPEN** | State check before call | Return cached/fallback response |
| **DB Connection Lost** | SQLAlchemy exception | Retry connection, fail after 3 attempts |
| **FAISS Index Corrupt** | Load exception | Rebuild index from PostgreSQL tasks |
| **Rate Limit Exceeded** | slowapi counter | Return 429 with Retry-After header |
| **Invalid JWT** | Decode exception | Return 401 Unauthorized |
| **RLS Violation** | PostgreSQL policy | Return 403 Forbidden, log to audit |

## Retry Configuration

```python
# Celery task retry configuration
@celery_app.task(bind=True, max_retries=3)
def process_message(self, ...):
    try:
        # Processing logic
        pass
    except Exception as e:
        if self.request.retries < self.max_retries:
            # Exponential backoff: 30s, 60s, 120s
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))
        else:
            # Final failure - return fallback response
            return {"success": False, "error": str(e)}
```
