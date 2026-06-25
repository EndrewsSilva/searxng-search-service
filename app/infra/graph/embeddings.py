"""
Embeddings locais com sentence-transformers (sem API, sem custo).
Modelo: paraphrase-multilingual-MiniLM-L12-v2
  - 384 dimensões
  - Suporte a 50+ idiomas incluindo português
  - ~120MB — carregado uma única vez (lazy singleton)
"""
import logging

logger = logging.getLogger(__name__)

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"[Embeddings] Carregando modelo {MODEL_NAME}...")
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("[Embeddings] Modelo carregado")
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Retorna lista de vetores float32 para cada texto."""
    if not texts:
        return []
    model = _get_model()
    vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_one(text: str) -> list[float]:
    result = embed([text])
    return result[0] if result else []
