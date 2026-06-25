import logging
import unicodedata
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


class ResultScorer:

    TRUSTED_DOMAINS = [
        "jusbrasil.com.br",
        "escavador.com",
        "econodata.com.br",
    ]

    NEGATIVE_TERMS = [
        "seguido por",
        "relacionado",
        "nomes relacionados",
        "parte que mais apareceu",
    ]

    @classmethod
    def score(cls, query: str, result: dict) -> dict:
        title = result.get("title", "")
        url = result.get("url", "")
        snippet = result.get("content", "")

        normalized_query = cls._normalize(query)
        normalized_title = cls._normalize(title)
        normalized_url = cls._normalize(url)
        normalized_snippet = cls._normalize(snippet)

        score = 0
        reasons = []

        if normalized_query in normalized_title:
            score += 50
            reasons.append("+50 título contém nome exato")

        if cls._query_in_url_slug(normalized_query, normalized_url):
            score += 30
            reasons.append("+30 URL contém nome no slug")

        if normalized_query in normalized_snippet:
            score += 20
            reasons.append("+20 snippet contém nome exato")

        domain = cls._extract_domain(url)
        if domain in cls.TRUSTED_DOMAINS:
            score += 20
            reasons.append("+20 domínio confiável")

        if cls._looks_like_other_person(normalized_query, normalized_title):
            score -= 40
            reasons.append("-40 título parece ser de outra pessoa")

        for term in cls.NEGATIVE_TERMS:
            if term in normalized_snippet:
                score -= 30
                reasons.append(f"-30 contém termo negativo: {term}")

        logger.info(
            "[SCORING] total=%s query='%s' title='%s' url='%s' reasons=%s",
            score,
            query,
            title,
            url,
            reasons,
        )

        return {
            "score": score,
            "reasons": reasons,
        }

    @staticmethod
    def _normalize(text: str) -> str:
        text = text or ""
        text = text.lower()

        text = unicodedata.normalize("NFKD", text)
        text = "".join(
            char for char in text
            if not unicodedata.combining(char)
        )

        return " ".join(text.split())

    @staticmethod
    def _extract_domain(url: str) -> str:
        domain = urlparse(url).netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    @staticmethod
    def _query_in_url_slug(query: str, url: str) -> bool:
        query_slug = query.replace(" ", "-")
        query_plus = query.replace(" ", "+")

        return query_slug in url or query_plus in url

    @staticmethod
    def _looks_like_other_person(query: str, title: str) -> bool:
        if query in title:
            return False

        # Se o título tem "processos" mas não tem o nome exato,
        # provavelmente é página de outra pessoa relacionada.
        if "processos" in title:
            return True

        return False
