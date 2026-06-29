"""
EscavadorAgent: scrapes escavador.com for professional profiles, academic history,
and judicial processes. Fetches the search page then the top individual profile pages.
"""
import asyncio
import logging
import re
from urllib.parse import quote

from .base import BaseSourceAgent, AgentResult

logger = logging.getLogger(__name__)

_PROFILE_RE = re.compile(r'href="(/sobre/\d+/[^"]+)"')


class EscavadorAgent(BaseSourceAgent):
    name = "escavador"
    source_type = "profile"
    BASE = "https://www.escavador.com"
    MAX_PROFILES = 3

    async def run(self, query: str) -> list[AgentResult]:
        results: list[AgentResult] = []

        search_url = f"{self.BASE}/busca?q={quote(query)}"
        logger.info(f"[{self.name}] search → {search_url}")
        search_html = await self._fetch(search_url)

        if not search_html:
            return []

        results.append(AgentResult(
            url=search_url,
            title=f"{query} - Escavador Busca",
            content=self._to_text(search_html),
            domain="escavador.com",
            source_type=self.source_type,
            raw_html=search_html,
            metadata={"source": "escavador", "page": "search"},
        ))

        profile_paths = list(dict.fromkeys(_PROFILE_RE.findall(search_html)))[: self.MAX_PROFILES]
        profile_urls = [f"{self.BASE}{p}" for p in profile_paths]

        if not profile_urls:
            return results

        logger.info(f"[{self.name}] fetching {len(profile_urls)} profile(s)")
        htmls = await asyncio.gather(*[self._fetch(u) for u in profile_urls], return_exceptions=True)

        for url, html in zip(profile_urls, htmls):
            if isinstance(html, Exception) or not html:
                continue
            results.append(AgentResult(
                url=url,
                title=f"{query} - Escavador Perfil",
                content=self._to_text(html),
                domain="escavador.com",
                source_type=self.source_type,
                raw_html=html,
                metadata={"source": "escavador", "page": "profile"},
            ))

        return results
