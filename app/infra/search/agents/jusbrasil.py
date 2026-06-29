"""
JusBrasilAgent: scrapes jusbrasil.com.br for judicial processes.
Uses authenticated session if available, falls back to FlareSolverr.
Fetches the search page then individual process detail pages.
"""
import asyncio
import logging
import re
from urllib.parse import quote

from app.infra.utils.detection import AntiBotDetector
from .base import BaseSourceAgent, AgentResult

logger = logging.getLogger(__name__)

_CNJ_RE = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
_MAX_PROCESS_PAGES = 5


class JusBrasilAgent(BaseSourceAgent):
    name = "jusbrasil"
    source_type = "legal"
    BASE = "https://www.jusbrasil.com.br"

    def __init__(self, flaresolverr_client, jusbrasil_session=None):
        super().__init__(flaresolverr_client)
        self.session = jusbrasil_session

    async def run(self, query: str) -> list[AgentResult]:
        results: list[AgentResult] = []

        search_url = f"{self.BASE}/busca?q={quote(query)}"
        logger.info(f"[{self.name}] → {search_url}")
        html = await self._fetch_jus(search_url)

        if not html:
            return []

        results.append(AgentResult(
            url=search_url,
            title=f"{query} - JusBrasil Busca",
            content=self._to_text(html),
            domain="jusbrasil.com.br",
            source_type=self.source_type,
            raw_html=html,
            metadata={"source": "jusbrasil", "page": "search"},
        ))

        # Follow individual process pages found in the search result
        from app.infra.search.process_link_extractor import ProcessLinkExtractor
        links = ProcessLinkExtractor.extract(html, search_url)
        process_urls = [
            u for u in links
            if "/processos/" in u and "/processos/nome/" not in u
        ][:_MAX_PROCESS_PAGES]

        if process_urls:
            logger.info(f"[{self.name}] fetching {len(process_urls)} process page(s)")
            htmls = await asyncio.gather(
                *[self._fetch_jus(u) for u in process_urls],
                return_exceptions=True,
            )
            for url, ph in zip(process_urls, htmls):
                if isinstance(ph, Exception) or not ph:
                    continue
                results.append(AgentResult(
                    url=url,
                    title=f"{query} - JusBrasil Processo",
                    content=self._to_text(ph),
                    domain="jusbrasil.com.br",
                    source_type=self.source_type,
                    raw_html=ph,
                    metadata={"source": "jusbrasil", "page": "process"},
                ))

        return results

    async def _fetch_jus(self, url: str) -> str:
        if self.session:
            try:
                html = await self.session.get_page(url)
                if html and not AntiBotDetector.is_blocked(html):
                    return html
                self.session.invalidate()
            except Exception as e:
                logger.warning(f"[{self.name}] session failed: {e}")
        return await self._fetch(url)
