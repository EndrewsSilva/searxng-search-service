"""
CnpjBizAgent: fetches company detail pages from cnpj.biz for CNPJs discovered by
other agents. Provides structured company data: QSA, activities, address, capital.
"""
import asyncio
import logging
import re

from .base import BaseSourceAgent, AgentResult

logger = logging.getLogger(__name__)


class CnpjBizAgent(BaseSourceAgent):
    name = "cnpjbiz"
    source_type = "cnpj"
    BASE = "https://cnpj.biz"
    MAX_CNPJS = 6

    async def run(self, query: str) -> list[AgentResult]:
        return []  # driven by run_for_cnpjs

    async def run_for_cnpjs(self, cnpjs: list[str]) -> list[AgentResult]:
        digits = list(dict.fromkeys(
            re.sub(r"\D", "", c) for c in cnpjs
        ))
        unique = [d for d in digits if len(d) == 14][: self.MAX_CNPJS]

        if not unique:
            return []

        urls = [f"{self.BASE}/{d}" for d in unique]
        logger.info(f"[{self.name}] fetching {len(urls)} CNPJ page(s)")
        htmls = await asyncio.gather(*[self._fetch(u) for u in urls], return_exceptions=True)

        results: list[AgentResult] = []
        for url, html in zip(urls, htmls):
            if isinstance(html, Exception) or not html:
                continue
            digits_str = url.split("/")[-1]
            cnpj_fmt = self._fmt(digits_str)
            results.append(AgentResult(
                url=url,
                title=f"CNPJ {cnpj_fmt} - CNPJ.biz",
                content=self._to_text(html),
                domain="cnpj.biz",
                source_type=self.source_type,
                raw_html=html,
                metadata={"source": "cnpjbiz", "cnpj": cnpj_fmt},
            ))

        return results

    @staticmethod
    def _fmt(digits: str) -> str:
        if len(digits) == 14:
            return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
        return digits
