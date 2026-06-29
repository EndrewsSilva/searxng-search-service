"""
AgentOrchestrator: runs all specialized source agents in parallel, aggregates
and ranks their results, then produces a unified SearchResponse for the GraphRAG
pipeline.

Agent pool:
  ConsultaSocioAgent  → company partnerships + homonym count
  EscavadorAgent      → professional profile, academic, judicial (search + profiles)
  JusBrasilAgent      → judicial processes (authenticated if session available)
  OpenWebAgent        → SearXNG multi-query for news / compliance signals
  CnpjBizAgent        → triggered after other agents discover CNPJs (lazy phase 2)
  DataJudClient       → CNJ official API enrichment for discovered process numbers
"""
import asyncio
import logging
import re
import unicodedata

from app.domain.models.search import SearchResponse, SearchResult, ProcessEntity
from app.infra.search.agents.base import AgentResult
from app.infra.search.agents.consultasocio import ConsultaSocioAgent
from app.infra.search.agents.escavador import EscavadorAgent
from app.infra.search.agents.cnpjbiz import CnpjBizAgent
from app.infra.search.agents.jusbrasil import JusBrasilAgent
from app.infra.search.agents.open_web import OpenWebAgent
from app.infra.search.process_entity_extractor import ProcessEntityExtractor
from app.infra.search.process_link_extractor import ProcessLinkExtractor

logger = logging.getLogger(__name__)

_CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
_CNJ_RE  = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")

# Domain-based base score weights (higher = more authoritative for compliance)
_DOMAIN_WEIGHT: dict[str, int] = {
    "consultasocio.com":        320,
    "cnpj.biz":                 300,
    "escavador.com":            270,
    "jusbrasil.com.br":         260,
    "advdinamico.com.br":       200,
    "casadosdados.com.br":      180,
    "cnpjcheck.com.br":         160,
    "cnpjaberto.com.br":        155,
    "empresas.serasaexperian.com.br": 150,
    "econodata.com.br":         130,
    "cnpj.info":                120,
}


class AgentOrchestrator:
    """
    Coordinates parallel source agents and converts their output into the
    SearchResponse expected by GraphBuilder + RagRetriever.
    """

    def __init__(
        self,
        flaresolverr_client,
        search_client,
        jusbrasil_session=None,
        datajud_client=None,
    ):
        self.consultasocio = ConsultaSocioAgent(flaresolverr_client)
        self.escavador     = EscavadorAgent(flaresolverr_client)
        self.cnpjbiz       = CnpjBizAgent(flaresolverr_client)
        self.jusbrasil     = JusBrasilAgent(flaresolverr_client, jusbrasil_session)
        self.open_web      = OpenWebAgent(flaresolverr_client, search_client)
        self.datajud       = datajud_client

    async def run(self, query: str) -> SearchResponse:
        logger.info(f"[AgentOrchestrator] starting for: {query!r}")

        # ── Phase 1: primary agents in parallel ───────────────────────────
        batches = await asyncio.gather(
            self.consultasocio.run(query),
            self.escavador.run(query),
            self.jusbrasil.run(query),
            self.open_web.run(query),
            return_exceptions=True,
        )

        primary: list[AgentResult] = []
        for i, batch in enumerate(batches):
            name = ["consultasocio", "escavador", "jusbrasil", "open_web"][i]
            if isinstance(batch, Exception):
                logger.error(f"[AgentOrchestrator] {name} failed: {batch}")
            else:
                logger.info(f"[AgentOrchestrator] {name}: {len(batch)} results")
                primary.extend(batch)

        # ── Phase 2: discover CNPJs → run CnpjBiz ─────────────────────────
        combined_text = " ".join(r.content + " " + r.raw_html for r in primary)
        cnpjs = list(dict.fromkeys(_CNPJ_RE.findall(combined_text)))[:8]

        if cnpjs:
            try:
                cnpj_results = await self.cnpjbiz.run_for_cnpjs(cnpjs)
                logger.info(f"[AgentOrchestrator] cnpjbiz: {len(cnpj_results)} pages for {len(cnpjs)} CNPJs")
                primary.extend(cnpj_results)
            except Exception as e:
                logger.error(f"[AgentOrchestrator] cnpjbiz failed: {e}")

        # ── Phase 3: dedup + rank ──────────────────────────────────────────
        all_results = self._dedup(primary)
        all_results = self._rank(all_results, query)
        logger.info(f"[AgentOrchestrator] {len(all_results)} unique results after ranking")

        # ── Phase 4: extract process entities from raw HTML ────────────────
        scraped_entities: list[ProcessEntity] = []
        for r in all_results:
            if not r.raw_html:
                continue
            try:
                for edata in ProcessEntityExtractor.extract_many(r.raw_html, r.url):
                    if self._is_valid_process(edata, query):
                        scraped_entities.append(ProcessEntity(**edata))
            except Exception as e:
                logger.warning(f"[AgentOrchestrator] process extractor error {r.url}: {e}")

        # ── Phase 5: DataJud enrichment ────────────────────────────────────
        datajud_entities: list[ProcessEntity] = []
        if self.datajud:
            cnj_numbers: list[str] = []
            for e in scraped_entities:
                cnj_numbers.extend(e.process_numbers or [])
            for r in all_results:
                cnj_numbers.extend(_CNJ_RE.findall(r.url))
            unique_cnj = list(dict.fromkeys(cnj_numbers))
            if unique_cnj:
                try:
                    datajud_entities = await self.datajud.enrich_by_numbers(unique_cnj)
                except Exception as e:
                    logger.error(f"[AgentOrchestrator] datajud failed: {e}")

        all_entities = self._dedup_processes(scraped_entities + datajud_entities)
        logger.info(f"[AgentOrchestrator] {len(all_entities)} unique process entities")

        # ── Phase 6: build SearchResult objects ───────────────────────────
        search_results = [
            SearchResult(
                title=r.title,
                url=r.url,
                snippet=r.content[:500],
                domain=r.domain,
                html_full=r.content,
                html_raw=None,
                is_exact_match=self._score(r, query) >= 300,
                matched_name=query if self._score(r, query) >= 300 else None,
                score=self._score(r, query),
                score_reasons=[r.source_type],
                process_links=ProcessLinkExtractor.extract(r.raw_html, r.url) if r.raw_html else [],
            )
            for r in all_results
        ]
        search_results.sort(key=lambda sr: sr.score or 0, reverse=True)

        return SearchResponse(
            query=query,
            total_found=len(search_results),
            results=search_results,
            process_entities=all_entities,
        )

    # ── helpers ──────────────────────────────────────────────────────────

    def _dedup(self, results: list[AgentResult]) -> list[AgentResult]:
        seen: set[str] = set()
        unique: list[AgentResult] = []
        for r in results:
            key = r.url.rstrip("/")
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def _rank(self, results: list[AgentResult], query: str) -> list[AgentResult]:
        return sorted(results, key=lambda r: self._score(r, query), reverse=True)

    def _score(self, r: AgentResult, query: str) -> int:
        score = _DOMAIN_WEIGHT.get(r.domain, 0)

        q_norm = _norm(query)
        if q_norm in _norm(r.title):
            score += 120
        if q_norm in _norm(r.content):
            score += 60

        slug = _slugify(query)
        if slug in r.url.lower():
            score += 80

        type_bonus = {"profile": 90, "cnpj": 80, "legal": 70, "academic": 60, "news": 20}
        score += type_bonus.get(r.source_type, 0)

        if _CNPJ_RE.search(r.content + r.raw_html):
            score += 40

        return score

    @staticmethod
    def _is_valid_process(edata: dict, query: str) -> bool:
        title = (edata.get("title") or "").lower()
        desc  = (edata.get("description") or "").lower()
        text  = f"{title} {desc}"

        if "nenhum processo encontrado" in text or "0 processos" in text:
            return False

        q_words = [w for w in query.lower().split() if len(w) > 3]
        matches = sum(1 for w in q_words if w in text)
        return matches >= min(3, max(2, len(q_words) // 2))

    @staticmethod
    def _dedup_processes(entities: list[ProcessEntity]) -> list[ProcessEntity]:
        seen: set[str] = set()
        unique: list[ProcessEntity] = []
        for e in entities:
            key = "|".join(e.process_numbers) if e.process_numbers else (e.source_url or "")
            if key and key not in seen:
                seen.add(key)
                unique.append(e)
        return unique


def _norm(text: str) -> str:
    t = (text or "").lower()
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


def _slugify(text: str) -> str:
    t = _norm(text)
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")
