"""
OpenWebAgent: uses SearXNG to run multiple compliance-oriented queries in parallel.
Covers open web sources: news, sanctions, PEP mentions, general mentions.
"""
import asyncio
import logging
import random
from urllib.parse import urlparse

from .base import BaseSourceAgent, AgentResult

logger = logging.getLogger(__name__)

_COMPLIANCE_TERMS = [
    "",                          # bare name search
    "processo judicial",
    "CNPJ empresa sócio",
    "fraude corrupção sanções",
    "mídia negativa PEP",
]

_NOISY_DOMAINS = frozenset({
    "bibliaonline.com.br", "bibliaon.com", "bibliaportugues.com",
    "oracaoefe.com.br", "dicionariodenomesproprios.com.br",
    "youtube.com", "wikipedia.org", "pt.wikipedia.org",
    "facebook.com", "vaticannews.va", "joaobidu.com.br",
})


class OpenWebAgent(BaseSourceAgent):
    name = "open_web"
    source_type = "news"

    MIN_DELAY = 2.5
    MAX_DELAY = 5.0

    def __init__(self, flaresolverr_client, search_client):
        super().__init__(flaresolverr_client)
        self.search_client = search_client

    async def run(self, query: str) -> list[AgentResult]:
        queries = [f'"{query}" {term}'.strip() for term in _COMPLIANCE_TERMS]

        results: list[AgentResult] = []
        seen: set[str] = set()

        for i, q in enumerate(queries):
            if i > 0:
                await asyncio.sleep(random.uniform(self.MIN_DELAY, self.MAX_DELAY))
            try:
                data = await self.search_client.search(q)
                for item in data.get("results", []):
                    url = item.get("url", "")
                    if not url or url in seen:
                        continue
                    domain = self._domain(url)
                    if domain in _NOISY_DOMAINS or any(domain.endswith(f".{d}") for d in _NOISY_DOMAINS):
                        continue
                    seen.add(url)
                    results.append(AgentResult(
                        url=url,
                        title=item.get("title", ""),
                        content=item.get("content", ""),
                        domain=domain,
                        source_type=self.source_type,
                        metadata={"source": "searxng", "query": q},
                    ))
            except Exception as e:
                logger.warning(f"[{self.name}] query '{q}' failed: {e}")

        logger.info(f"[{self.name}] {len(results)} unique results from {len(queries)} queries")
        return results
