"""
TaskPilot FAISS Index Manager
Векторный поиск в памяти Python с персистентностью на диск
"""

import os
import pickle
import numpy as np
import faiss
from typing import List, Dict, Tuple, Optional
from sentence_transformers import SentenceTransformer
from app.config import settings
import structlog

logger = structlog.get_logger(__name__)

class FAISSIndexManager:
    """Управление FAISS индексом для семантического поиска задач"""
    
    def __init__(self):
        self.index_path = settings.FAISS_INDEX_PATH
        self.mapping_path = os.path.join(self.index_path, "id_mapping.pkl")
        self.index_file = os.path.join(self.index_path, "faiss.index")
        
        # Загрузка модели эмбеддингов
        logger.info("faiss_loading_embedding_model", model=settings.EMBEDDING_MODEL)
        self.model = SentenceTransformer(settings.EMBEDDING_MODEL)
        self.dimension = settings.EMBEDDING_DIMENSION
        
        # FAISS индекс (Flat Inner Product для косинусного сходства)
        self.index = faiss.IndexFlatIP(self.dimension)
        
        # Mapping: FAISS index position → Task UUID
        self.id_mapping: Dict[int, str] = {}
        
        # Обратный mapping: Task UUID → FAISS index position
        self.uuid_to_idx: Dict[str, int] = {}
        
        # Загрузка существующего индекса
        self._load_index()
    
    def _load_index(self):
        """Загрузка индекса с диска при старте"""
        os.makedirs(self.index_path, exist_ok=True)
        
        if os.path.exists(self.index_file):
            try:
                self.index = faiss.read_index(self.index_file)
                logger.info("faiss_index_loaded", vectors=self.index.ntotal)
            except Exception as e:
                logger.warning("faiss_index_load_failed", error=str(e))
        
        if os.path.exists(self.mapping_path):
            try:
                with open(self.mapping_path, "rb") as f:
                    self.id_mapping = pickle.load(f)
                    self.uuid_to_idx = {v: k for k, v in self.id_mapping.items()}
                logger.info("faiss_mapping_loaded", entries=len(self.id_mapping))
            except Exception as e:
                logger.warning("faiss_mapping_load_failed", error=str(e))
    
    def _save_index(self):
        """Сохранение индекса на диск"""
        try:
            faiss.write_index(self.index, self.index_file)
            with open(self.mapping_path, "wb") as f:
                pickle.dump(self.id_mapping, f)
            logger.debug("faiss_index_saved", vectors=self.index.ntotal)
        except Exception as e:
            logger.error("faiss_index_save_failed", error=str(e))
    
    def add_task(self, task_id: str, title: str, description: str = ""):
        """
        Добавление задачи в индекс
        
        Args:
            task_id: UUID задачи
            title: Заголовок задачи
            description: Описание задачи
        """
        try:
            # Создание эмбеддинга
            text = f"{title} {description}".strip()
            embedding = self.model.encode([text], normalize_embeddings=True)[0]
            
            # Добавление в FAISS
            idx = self.index.ntotal
            self.index.add(embedding.reshape(1, -1))
            
            # Обновление mapping
            self.id_mapping[idx] = task_id
            self.uuid_to_idx[task_id] = idx
            
            # Сохранение на диск (для MVP — каждый раз)
            self._save_index()
            
            logger.info("faiss_task_added", task_id=task_id, total_vectors=self.index.ntotal)
            
        except Exception as e:
            logger.error("faiss_add_task_failed", task_id=task_id, error=str(e))
    
    def search(
        self, 
        query: str, 
        k: int = 5,
        group_id: Optional[str] = None
    ) -> List[Tuple[str, float]]:
        """
        Семантический поиск задач
        
        Args:
            query: Текст запроса
            k: Количество результатов
            group_id: Фильтр по группе (опционально, применяется на уровне БД)
        
        Returns:
            Список кортежей (task_id, score)
        """
        try:
            # Векторизация запроса
            query_embedding = self.model.encode([query], normalize_embeddings=True)[0]
            
            # Поиск в FAISS
            scores, indices = self.index.search(
                query_embedding.reshape(1, -1), 
                k=k
            )
            
            # Конвертация в task_id
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0:  # FAISS возвращает -1 если не нашёл k результатов
                    continue
                
                task_id = self.id_mapping.get(idx)
                if task_id:
                    results.append((task_id, float(score)))
            
            logger.debug("faiss_search_completed", query=query, results=len(results))
            return results
            
        except Exception as e:
            logger.error("faiss_search_failed", query=query, error=str(e))
            return []
    
    def get_stats(self) -> Dict:
        """Статистика индекса"""
        return {
            "total_vectors": self.index.ntotal,
            "dimension": self.dimension,
            "index_type": type(self.index).__name__,
            "memory_usage_mb": round(self.index.ntotal * self.dimension * 4 / 1024 / 1024, 2),
            "mapping_entries": len(self.id_mapping)
        }

# ============================================================================
# Singleton Instance
# ============================================================================

# Глобальный экземпляр (переиспользуется между воркерами)
faiss_index = FAISSIndexManager()