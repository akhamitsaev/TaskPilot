"""
TaskPilot Agent Core
LLM-анализ сообщений: классификация, извлечение сущностей, генерация ответов
Обновлено: временной контекст + явные правила приоритетов + Circuit Breaker + TTFT/TPOT метрики
"""

import json
import time  # ← ДОБАВЛЕНО для метрик
from typing import Dict, List, Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field
from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage
from app.config import settings
from app.infrastructure import llm_circuit_breaker, CircuitBreakerOpenError
from app.infrastructure.metrics import LLMMetricsTracker  # ← ДОБАВЛЕНО для метрик
import structlog

logger = structlog.get_logger(__name__)

# ============================================================================
# Pydantic Models (для валидации LLM-ответов)
# ============================================================================

class TaskEntity(BaseModel):
    """Извлечённая задача из сообщения"""
    title: str = Field(..., description="Краткий заголовок задачи")
    description: str = Field(default="", description="Подробное описание")
    deadline: Optional[str] = Field(None, description="Дедлайн в формате ISO 8601")
    priority: int = Field(default=5, ge=1, le=10, description="Приоритет 1-10")
    problem: Optional[str] = Field(None, description="Проблема/препятствие если есть")
    dependencies: List[str] = Field(default=[], description="Названия зависимых задач")


class AgentResponse(BaseModel):
    """Ответ агента после анализа сообщения"""
    is_task: bool = Field(..., description="Является ли сообщение задачей")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Уверенность классификации")
    task: Optional[TaskEntity] = Field(None, description="Извлечённая задача если is_task=True")
    response_text: str = Field(..., description="Текстовый ответ пользователю")
    requires_clarification: bool = Field(default=False, description="Нужно ли уточнение у пользователя")
    clarification_question: Optional[str] = Field(None, description="Вопрос для уточнения")


# ============================================================================
# Agent Class
# ============================================================================

class TaskAgent:
    """
    Основной агент для анализа сообщений и извлечения задач
    
    Использует Mistral API для:
    1. Классификации: сообщение = задача или нет
    2. Извлечения сущностей: заголовок, дедлайн, проблема, зависимости
    3. Генерации ответа пользователю
    
    Защищён Circuit Breaker для отказоустойчивости (Infrastructure Track)
    """
    
    def __init__(self):
        self.client = MistralClient(api_key=settings.MISTRAL_API_KEY)
        self.model = settings.MISTRAL_MODEL
        self.timeout = settings.LLM_TIMEOUT
        self.max_tokens = settings.LLM_MAX_TOKENS
        self.temperature = settings.LLM_TEMPERATURE
    
    def classify_and_extract(self, message: str, context: Optional[List[Dict]] = None) -> AgentResponse:
        """
        Анализ сообщения: классификация + извлечение сущностей
        
        Args:
            message: Текст сообщения от пользователя
            context: Контекст (последние задачи пользователя для связывания)
        
        Returns:
            AgentResponse с результатом анализа
        """
        # Формирование промпта
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(message, context)
        
        try:
            # Создание трекера метрик (TTFT/TPOT/Cost) ← ДОБАВЛЕНО
            tracker = LLMMetricsTracker(self.model)
            tracker.start_request()
            
            # Вызов LLM через Circuit Breaker (защита от каскадных сбоев)
            try:
                response = llm_circuit_breaker.call(
                    self.client.chat,
                    model=self.model,
                    messages=[
                        ChatMessage(role="system", content=system_prompt),
                        ChatMessage(role="user", content=user_prompt)
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    response_format={"type": "json_object"}  # Гарантируем JSON
                )
                
                # Запись первого токена (TTFT) ← ДОБАВЛЕНО
                tracker.record_first_token()
                
            except CircuitBreakerOpenError as e:
                # Circuit Breaker разомкнут — LLM недоступен
                logger.warning(
                    "circuit_breaker_open_llm_blocked",
                    error=str(e),
                    fallback="returning_error_response"
                )
                return AgentResponse(
                    is_task=False,
                    confidence=0.5,
                    response_text="Сервис временно недоступен. Попробуйте через минуту.",
                    requires_clarification=False
                )
            
            # Парсинг ответа
            llm_output = response.choices[0].message.content
            logger.info("llm_response_received", tokens_used=response.usage.total_tokens)
            
            # Завершение запроса с метриками (TPOT/Cost/Tokens) ← ДОБАВЛЕНО
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
            tracker.complete_request(input_tokens, output_tokens, status="success")
            
            # Валидация через Pydantic
            agent_response = AgentResponse.model_validate_json(llm_output)
            
            return agent_response
            
        except Exception as e:
            logger.error("llm_analysis_failed", error=str(e))
            # Fallback: возвращаем безопасный ответ
            return AgentResponse(
                is_task=False,
                confidence=0.5,
                response_text="Извините, произошла ошибка при анализе сообщения. Попробуйте ещё раз.",
                requires_clarification=False
            )
    
    def _build_system_prompt(self) -> str:
        """Усиленный системный промпт с явными требованиями к формату"""
        return """
Ты — автономный ассистент управления задачами TaskPilot.
Твоя цель: анализировать сообщения пользователей и определять, является ли сообщение задачей.

=== ПРАВИЛА КЛАССИФИКАЦИИ ===
- Задача = конкретное действие с дедлайном или целью ("сделать отчёт до пятницы", "позвонить клиенту")
- Не задача = вопрос, обсуждение, информация без действия ("как дела?", "спасибо", "понял")
- Если сомневаешься — классифицируй как "не задача" (is_task=false)

=== ИЗВЛЕЧЕНИЕ СУЩНОСТЕЙ (только если is_task=true) ===
- title: краткий заголовок (2-5 слов), без артиклей и лишних слов
- description: подробное описание (1-2 предложения), только если есть детали в сообщении
- deadline: дата в формате ISO 8601 (YYYY-MM-DDTHH:MM:SS). Если относительное время ("завтра", "до пятницы") — вычисли абсолютную дату на основе [Контекст времени]
- priority: число 1-10 (10 = срочно). Смотри раздел "ПРАВИЛА ОЦЕНКИ ПРИОРИТЕТА" ниже
- problem: текст препятствия если упоминается ("жду данные", "нет доступа", "зависит от Иванова")
- dependencies: массив названий других задач если есть явные ссылки ("после задачи А", "когда будет готов отчёт Б")

=== ПРАВИЛА ОЦЕНКИ ПРИОРИТЕТА (ОБЯЗАТЕЛЬНО УЧИТЫВАЙ [Контекст времени]) ===
| Ситуация | Приоритет | Пример |
|----------|-----------|--------|
| Дедлайн "сегодня до вечера" | 10 | "Сдать до 18:00" |
| Дедлайн "завтра" (если сейчас вечер предыдущего дня) | 9-10 | Четверг 20:00 → "до пятницы" |
| Дедлайн "до пятницы" (если сегодня четверг после 12:00) | 8-9 | Четверг 14:30 → "до пятницы" |
| Дедлайн "до пятницы" (если сегодня понедельник-среда) | 5-7 | Понедельник → "до пятницы" |
| Дедлайн в пределах текущей недели (не завтра) | 5-7 | "До конца недели" |
| Дедлайн "на следующей неделе" | 3-4 | "На следующей неделе" |
| Дедлайн "когда-нибудь" / нет дедлайна | 2-3 | "Когда будет время" |
| Упоминание "срочно", "как можно быстрее" | +2 к базовому приоритету | "Срочно сделать отчёт" |

=== ТРЕБОВАНИЯ К ФОРМАТУ ОТВЕТА (КРИТИЧНО!) ===
Ты ДОЛЖЕН вернуть ВАЛИДНЫЙ JSON, строго соответствующий этой схеме:

{
  "is_task": true или false,
  "confidence": число от 0.0 до 1.0 (0.5 если не уверен),
  "task": {
    "title": "строка",
    "description": "строка или пусто",
    "deadline": "YYYY-MM-DDTHH:MM:SS или null",
    "priority": число 1-10,
    "problem": "строка или null",
    "dependencies": ["строка"] или []
  } или null (если is_task=false),
  "response_text": "строка, всегда! даже если короткая: 'ОК', 'Понял', 'Создал задачу'",
  "requires_clarification": true или false,
  "clarification_question": "строка или null"
}

ПРАВИЛА ЗАПОЛНЕНИЯ ПОЛЕЙ:
1. confidence: ВСЕГДА число. Если не уверен — ставь 0.5. Никогда не пропускай.
2. response_text: ВСЕГДА строка. Даже если is_task=true — напиши "Задача создана" или "Обновил задачу".
3. task: null если is_task=false. Заполняй только если is_task=true.
4. priority: ВСЕГДА число 1-10. Используй таблицу выше + контекст времени.
5. Все строковые поля: пустая строка "" если нет значения, не null (кроме deadline/problem/clarification_question).

=== ПРИМЕРЫ (FEW-SHOT) ===

Пример 1:
[Контекст времени] Сегодня: четверг, 2026-04-09, 14:30
Сообщение: "Нужно подготовить отчёт до пятницы, жду данные от Иванова"
Ответ:
{
  "is_task": true,
  "confidence": 0.95,
  "task": {
    "title": "Подготовить отчёт",
    "description": "",
    "deadline": "2026-04-10T23:59:59",
    "priority": 9,
    "problem": "жду данные от Иванова",
    "dependencies": []
  },
  "response_text": "Создал задачу 'Подготовить отчёт' с дедлайном завтра. Отслеживаю проблему: ожидание данных от Иванова.",
  "requires_clarification": false,
  "clarification_question": null
}

Пример 2:
[Контекст времени] Сегодня: понедельник, 2026-04-07, 10:00
Сообщение: "Сделать до пятницы"
Ответ:
{
  "is_task": true,
  "confidence": 0.8,
  "task": {
    "title": "Выполнить задачу",
    "description": "",
    "deadline": "2026-04-11T23:59:59",
    "priority": 6,
    "problem": null,
    "dependencies": []
  },
  "response_text": "Задача с дедлайном до пятницы создана. Уточните детали, если нужно.",
  "requires_clarification": true,
  "clarification_question": "Что именно нужно сделать до пятницы?"
}

Пример 3:
[Контекст времени] Сегодня: любой день
Сообщение: "Спасибо за помощь!"
Ответ:
{
  "is_task": false,
  "confidence": 0.99,
  "task": null,
  "response_text": "Всегда пожалуйста! Если появятся новые задачи — пиши.",
  "requires_clarification": false,
  "clarification_question": null
}

Пример 4 (неоднозначность):
[Контекст времени] Сегодня: четверг, 14:30
Сообщение: "Обнови ту задачу по маркетингу"
Ответ:
{
  "is_task": true,
  "confidence": 0.6,
  "task": null,
  "response_text": "Нашёл несколько задач по маркетингу. Уточните, пожалуйста.",
  "requires_clarification": true,
  "clarification_question": "Какую именно задачу по маркетингу вы имеете в виду: 'Запустить рекламу' или 'Подготовить презентацию'?"
}

=== ФИНАЛЬНАЯ ПРОВЕРКА ПЕРЕД ОТВЕТОМ ===
Перед тем как вернуть JSON, проверь:
□ is_task: boolean (true/false)
□ confidence: число 0.0-1.0 (не null, не строка!)
□ response_text: строка (не null, не пусто! минимум "ОК")
□ Если is_task=true → task не null, priority 1-10
□ Если is_task=false → task = null
□ Все поля присутствуют, нет пропущенных ключей

Теперь проанализируй сообщение пользователя и верни ТОЛЬКО валидный JSON, без markdown, без пояснений, без ```json обёртки.
""".strip()
    
    def _build_user_prompt(self, message: str, context: Optional[List[Dict]] = None) -> str:
        """Пользовательский промпт с контекстом и временной информацией"""
        
        # Добавляем текущее время (ключевое для оценки приоритетов!)
        now = datetime.now()
        time_context = f"""
[Контекст времени]
Сегодня: {now.strftime('%A, %Y-%m-%d')}
Время: {now.strftime('%H:%M')}
Часовой пояс: UTC+3 (Москва)
"""
        
        prompt = time_context + f"\nСообщение пользователя: {message}\n\n"
        
        if context:
            prompt += "Контекст (последние задачи пользователя):\n"
            for i, task in enumerate(context[:5], 1):  # Максимум 5 задач
                prompt += f"{i}. {task.get('title', 'N/A')} (статус: {task.get('status', 'N/A')})\n"
            prompt += "\n"
        
        prompt += "Проанализируй сообщение и верни JSON согласно схеме AgentResponse."
        prompt += "\n\nВАЖНО: Учитывай текущую дату и время при оценке дедлайнов и приоритетов!"
        
        return prompt
    
    def generate_summary(self, task_data: Dict, dependencies: List[Dict]) -> str:
        """
        Генерация сводки по задаче
        
        Args:
            task_data: Данные задачи из БД
            dependencies: Список зависимых задач
        
        Returns:
            Текстовая сводка для пользователя
        """
        prompt = f"""
Создай краткую сводку по задаче:

Задача: {task_data.get('title', 'N/A')}
Статус: {task_data.get('status', 'N/A')}
Дедлайн: {task_data.get('deadline', 'N/A')}
Проблема: {task_data.get('problem', 'Нет проблем')}

Зависимости:
{json.dumps(dependencies, ensure_ascii=False, indent=2) if dependencies else 'Нет зависимостей'}

Сводка должна быть на русском языке, 2-3 предложения.
"""
        
        try:
            # Вызов LLM через Circuit Breaker
            try:
                response = llm_circuit_breaker.call(
                    self.client.chat,
                    model=self.model,
                    messages=[ChatMessage(role="user", content=prompt)],
                    temperature=0.3,
                    max_tokens=500
                )
                return response.choices[0].message.content
            except CircuitBreakerOpenError as e:
                logger.warning("circuit_breaker_open_summary_blocked", error=str(e))
                return f"Задача: {task_data.get('title', 'N/A')}, Статус: {task_data.get('status', 'N/A')} (сервис временно недоступен)"
                
        except Exception as e:
            logger.error("summary_generation_failed", error=str(e))
            return f"Задача: {task_data.get('title', 'N/A')}, Статус: {task_data.get('status', 'N/A')}"


# ============================================================================
# Singleton Instance
# ============================================================================

# Глобальный экземпляр агента (переиспользуется между задачами Celery)
agent = TaskAgent()


# ============================================================================
# Helper Functions (для удобства вызова из Celery tasks)
# ============================================================================

def analyze_message(message: str, context: Optional[List[Dict]] = None) -> AgentResponse:
    """Обёртка для вызова агента из Celery задач"""
    return agent.classify_and_extract(message, context)


def generate_task_summary(task_data: Dict, dependencies: List[Dict]) -> str:
    """Обёртка для генерации сводки"""
    return agent.generate_summary(task_data, dependencies)