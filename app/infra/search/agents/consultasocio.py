"""
ConsultaSocioAgent: scrapes consultasocio.com for company partnerships.
Produces the profile page that lists all companies where the person is a partner.
"""
import logging

from .base import BaseSourceAgent, AgentResult

logger = logging.getLogger(__name__)


class ConsultaSocioAgent(BaseSourceAgent):
    name = "consultasocio"
    source_type = "profile"
    BASE = "https://www.consultasocio.com"

    async def run(self, query: str) -> list[AgentResult]:
        slug = self._slugify(query)
        url = f"{self.BASE}/q/sa/{slug}"
        logger.info(f"[{self.name}] → {url}")

        html = await self._fetch(url)
        if not html:
            return []

        return [AgentResult(
            url=url,
            title=f"{query} - ConsultaSocio",
            content=self._to_text(html),
            domain="consultasocio.com",
            source_type=self.source_type,
            raw_html=html,
            metadata={"source": "consultasocio", "query": query},
        )]
