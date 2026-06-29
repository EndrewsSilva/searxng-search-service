"""
Embeddings locais com sentence-transformers (sem API, sem custo).
Modelo: paraphrase-multilingual-MiniLM-L12-v2
  - 384 dimensões
  - Suporte a 50+ idiomas incluindo português
  - ~120MB — carregado uma única vez (lazy singleton, thread-safe)
"""
import asyncio
import logging
import threading

logger = logging.getLogger(__name__)

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_model = None
_model_lock = threading.Lock()


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"[Embeddings] Carregando modelo {MODEL_NAME}...")
            _model = SentenceTransformer(MODEL_NAME)
            logger.info("[Embeddings] Modelo carregado")
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Retorna lista de vetores float32 para cada texto (síncrono, bloqueia thread)."""
    if not texts:
        return []
    model = _load_model()
    vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_one(text: str) -> list[float]:
    result = embed([text])
    return result[0] if result else []


async def embed_async(texts: list[str]) -> list[list[float]]:
    """Versão assíncrona: executa em thread pool para não bloquear o event loop."""
    if not texts:
        return []
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed, texts)


async def embed_one_async(text: str) -> list[float]:
    result = await embed_async([text])
    return result[0] if result else []


async def prewarm_async():
    """Dispara o carregamento do modelo em background (chamar no startup)."""
    loop = asyncio.get_running_loop()
    logger.info("[Embeddings] Pre-aquecendo modelo em background...")
    await loop.run_in_executor(None, _load_model)
    logger.info("[Embeddings] Pre-aquecimento concluído")
