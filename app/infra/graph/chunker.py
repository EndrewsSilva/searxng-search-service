"""
Divide texto em chunks sobrepostos para indexação no grafo.
Sem dependências externas — implementação pura Python.
"""
import re
import uuid


class TextChunker:
    def __init__(self, chunk_size: int = 400, overlap: int = 80):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str, source_url: str = "", source_type: str = "web") -> list[dict]:
        if not text or not text.strip():
            return []

        sentences = self._split_sentences(text)
        chunks = []
        current = []
        current_len = 0

        for sentence in sentences:
            sentence_len = len(sentence.split())

            if current_len + sentence_len > self.chunk_size and current:
                chunk_text = " ".join(current).strip()
                if chunk_text:
                    chunks.append(self._make_chunk(chunk_text, source_url, source_type))

                # Overlap: mantém as últimas sentenças
                overlap_sentences = []
                overlap_len = 0
                for s in reversed(current):
                    s_len = len(s.split())
                    if overlap_len + s_len <= self.overlap:
                        overlap_sentences.insert(0, s)
                        overlap_len += s_len
                    else:
                        break

                current = overlap_sentences
                current_len = overlap_len

            current.append(sentence)
            current_len += sentence_len

        if current:
            chunk_text = " ".join(current).strip()
            if chunk_text:
                chunks.append(self._make_chunk(chunk_text, source_url, source_type))

        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        text = re.sub(r"\s+", " ", text).strip()
        sentences = re.split(r"(?<=[.!?])\s+", text)
        result = []
        for s in sentences:
            if len(s.split()) > 60:
                # Quebra sentenças muito longas por pontuação secundária
                parts = re.split(r",\s*|\s*;\s*|\s*—\s*", s)
                result.extend(p.strip() for p in parts if p.strip())
            else:
                if s.strip():
                    result.append(s.strip())
        return result

    @staticmethod
    def _make_chunk(text: str, source_url: str, source_type: str) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "text": text,
            "source_url": source_url,
            "source_type": source_type,
        }
