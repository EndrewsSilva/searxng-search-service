"""
Orquestrador do relatório de investigação — arquitetura multiagente.

Fluxo:
  1. AgentOrchestrator executa agentes especializados em paralelo:
       ConsultaSocioAgent, EscavadorAgent, JusBrasilAgent, OpenWebAgent
     → Fase 2 (lazy): CnpjBizAgent para CNPJs descobertos
     → Fase 5: DataJud para enriquecer números de processo
  2. SearchResponse unificado → GraphBuilder popula Neo4j
  3. Por seção do relatório:
       a. RagRetriever: Cypher no grafo + busca vetorial semântica
       b. ReportGenerator: geração via HF Mistral-7B ou fallback estruturado
  4. Compila referências numeradas [N] a partir de todas as URLs fonte
  5. Monta e retorna ComplianceReport
"""
import logging
from datetime import datetime, timezone

from app.domain.models.compliance import ComplianceReport, ComplianceSection, GraphStats
from app.infra.search.agent_orchestrator import AgentOrchestrator
from app.infra.graph.neo4j_client import Neo4jClient
from app.infra.graph.graph_builder import GraphBuilder, _person_id
from app.infra.graph.rag_retriever import RagRetriever, SECTIONS as SECTION_DEFS
from app.infra.graph.report_generator import ReportGenerator, SECTIONS as REPORT_SECTIONS
from app.infra.graph.entity_extractor import EntityExtractor

logger = logging.getLogger(__name__)


class RunComplianceReportUseCase:
    def __init__(
        self,
        agent_orchestrator: AgentOrchestrator,
        neo4j: Neo4jClient,
        hf_token: str = "",
    ):
        self.orchestrator = agent_orchestrator
        self.neo4j = neo4j
        self.hf_token = hf_token
        self.extractor = EntityExtractor(hf_token)
        self.builder = GraphBuilder(neo4j, self.extractor)
        self.retriever = RagRetriever(neo4j)
        self.generator = ReportGenerator(hf_token)

    async def execute(self, query: str) -> ComplianceReport:
        logger.info(f"[ComplianceReport] Iniciando relatório multiagente para: {query!r}")

        # ── 1. Executa agentes em paralelo ────────────────────────────────
        search_response = await self.orchestrator.run(query)
        logger.info(
            f"[ComplianceReport] Busca concluída: {len(search_response.results)} resultados, "
            f"{len(search_response.process_entities)} processos"
        )

        # ── 2. Constrói/atualiza o grafo Neo4j ────────────────────────────
        stats = await self.builder.build(query, search_response)
        target_id = _person_id(query)

        # ── 3. Pré-carrega todos os contextos para montar mapa de referências ──
        section_contexts: dict[str, dict] = {}
        for section_key, section_meta in REPORT_SECTIONS.items():
            try:
                ctx = await self.retriever.retrieve_for_section(
                    section_key=section_key,
                    target_id=target_id,
                    semantic_query=section_meta["semantic_query"],
                )
                section_contexts[section_key] = ctx
            except Exception as e:
                logger.error(f"[ComplianceReport] Contexto '{section_key}' falhou: {e}")
                section_contexts[section_key] = {"graph_data": [], "chunks": []}

        # ── 4. Monta mapa global URL → número de referência ───────────────
        url_to_ref, references_list = _build_reference_map(
            section_contexts, search_response.results
        )

        # ── 5. Gera cada seção do relatório ──────────────────────────────
        sections: dict[str, ComplianceSection] = {}
        overall_risks: list[str] = []

        for section_key, section_meta in REPORT_SECTIONS.items():
            logger.info(f"[ComplianceReport] Gerando seção: {section_meta['title']}")
            ctx = section_contexts[section_key]
            try:
                content, risk_level = await self.generator.generate_section(
                    section_key=section_key,
                    target=query,
                    graph_data=ctx["graph_data"],
                    chunks=ctx["chunks"],
                    url_to_ref=url_to_ref,
                )

                sources = list({
                    c.get("source_url", "") for c in ctx["chunks"] if c.get("source_url")
                })

                sections[section_key] = ComplianceSection(
                    title=section_meta["title"],
                    content=content,
                    sources=sources,
                    risk_level=risk_level,
                )

                if risk_level not in ("N/A", "LOW", None):
                    overall_risks.append(risk_level)

            except Exception as e:
                logger.error(f"[ComplianceReport] Seção '{section_key}' falhou: {e}")
                sections[section_key] = ComplianceSection(
                    title=REPORT_SECTIONS[section_key]["title"],
                    content="Erro ao gerar esta seção.",
                    risk_level="N/A",
                )

        # ── 6. Risco geral ────────────────────────────────────────────────
        risk_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "N/A": 0}
        overall = max(overall_risks, key=lambda r: risk_order.get(r, 0), default="N/A")

        # ── 7. Estatísticas do grafo ──────────────────────────────────────
        graph_stats_raw = await self.retriever.get_graph_stats(target_id)
        graph_stats = GraphStats(
            persons=1,
            companies=stats.get("companies", 0),
            processes=graph_stats_raw.get("processes", stats.get("processes", 0)),
            events=graph_stats_raw.get("events", stats.get("events", 0)),
            chunks=graph_stats_raw.get("chunks", stats.get("chunks", 0)),
            relationships=stats.get("relationships", 0),
        )

        return ComplianceReport(
            query=query,
            generated_at=datetime.now(timezone.utc).isoformat(),
            sections=sections,
            overall_risk=overall,
            graph_stats=graph_stats,
            raw_process_count=len(search_response.process_entities),
            references=references_list,
        )


def _build_reference_map(
    section_contexts: dict[str, dict],
    search_results,
) -> tuple[dict[str, int], list[dict]]:
    """
    Builds a global URL→[N] map from all chunk source_urls across sections.
    Profile/CNPJ/legal sources appear first; open-web results fill remaining slots.
    Returns (url_to_ref, ordered references list).
    """
    # Collect URLs ordered by source priority
    priority_domains = {
        "consultasocio.com", "cnpj.biz", "escavador.com",
        "jusbrasil.com.br", "advdinamico.com.br", "casadosdados.com.br",
    }

    title_map: dict[str, str] = {}
    for r in (search_results or []):
        url = getattr(r, "url", "") or ""
        title = getattr(r, "title", "") or ""
        if url:
            title_map[url] = title

    # Gather all URLs across sections in order
    seen: dict[str, int] = {}   # canonical_url → ref num
    refs: list[dict] = []

    def _canonical(raw_url: str) -> str:
        """Strip tracking query params (msockid, utm_*, fbclid, etc.) for dedup."""
        from urllib.parse import urlparse, urlencode, parse_qsl
        _TRACKING = {"msockid", "utm_source", "utm_medium", "utm_campaign",
                     "fbclid", "gclid", "ref", "source"}
        p = urlparse(raw_url)
        qs = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in _TRACKING]
        clean = p._replace(query=urlencode(qs))
        return clean.geturl().rstrip("/")

    def _add(url: str):
        if not url:
            return
        canon = _canonical(url)
        orig  = url.rstrip("/")
        if canon in seen:
            if orig not in seen:
                seen[orig] = seen[canon]
            return
        num = len(refs) + 1
        seen[canon] = num
        seen[orig]  = num
        label = _ref_label(url, title_map)
        refs.append({"num": num, "label": label, "url": url})

    # Pass 1: priority sources
    for ctx in section_contexts.values():
        for chunk in ctx.get("chunks", []):
            url = chunk.get("source_url", "") or ""
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower().lstrip("www.")
            if domain in priority_domains:
                _add(url)

    # Pass 2: remaining sources
    for ctx in section_contexts.values():
        for chunk in ctx.get("chunks", []):
            _add(chunk.get("source_url", "") or "")

    return seen, refs


def _ref_label(url: str, title_map: dict[str, str]) -> str:
    if title_map.get(url):
        return title_map[url][:80]
    from urllib.parse import urlparse
    p = urlparse(url)
    return f"{p.netloc}{p.path[:50]}"
