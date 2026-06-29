"""
BaseSourceAgent: abstract base class for all specialized source scrapers.
Each subclass targets a specific domain/source and returns AgentResult list.
"""
import asyncio
import logging
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.infra.search.html_parser import HTMLContentParser
from app.infra.utils.detection import AntiBotDetector

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    url: str
    title: str
    content: str       # cleaned plain text
    domain: str
    source_type: str   # "profile" | "legal" | "cnpj" | "news" | "academic"
    raw_html: str = ""
    metadata: dict = field(default_factory=dict)


class BaseSourceAgent(ABC):
    name: str = "base"
    source_type: str = "general"

    def __init__(self, flaresolverr_client):
        self.client = flaresolverr_client
        self._semaphore = asyncio.Semaphore(2)

    @abstractmethod
    async def run(self, query: str) -> list[AgentResult]:
        """Run agent and return results for this source."""

    async def _fetch(self, url: str) -> str:
        async with self._semaphore:
            try:
                html = await self.client.get_page(url)
                if not html or AntiBotDetector.is_blocked(html):
                    return ""
                return html
            except Exception as e:
                logger.warning(f"[{self.name}] fetch failed {url}: {e}")
                return ""

    def _to_text(self, html: str) -> str:
        return HTMLContentParser.clean_html_to_text(html) if html else ""

    @staticmethod
    def _domain(url: str) -> str:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d

    @staticmethod
    def _slugify(text: str) -> str:
        t = unicodedata.normalize("NFKD", text or "")
        t = "".join(c for c in t if not unicodedata.combining(c)).lower()
        return re.sub(r"[^a-z0-9]+", "-", t).strip("-")
