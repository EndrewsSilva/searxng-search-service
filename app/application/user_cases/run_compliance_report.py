"""
Orquestrador do relatório de compliance Graph RAG.

Fluxo:
  1. Busca (RunSearchUseCase) → SearchResponse
  2. Constrói grafo Neo4j (GraphBuilder)
  3. Para cada seção do relatório:
     a. Recupera contexto via RAG (RagRetriever): Cypher + vector search
     b. Gera texto da seção (ReportGenerator via HF Mistral-7B)
  4. Monta e retorna ComplianceReport
"""
import logging
from datetime import datetime, timezone

from app.application.user_cases.run_search import RunSearchUseCase
from app.domain.models.compliance import ComplianceReport, ComplianceSection, GraphStats
from app.infra.graph.neo4j_client import Neo4jClient
from app.infra.graph.schema import setup_schema
from app.infra.graph.graph_builder import GraphBuilder, _person_id
from app.infra.graph.rag_retriever import RagRetriever, SECTIONS as SECTION_DEFS
from app.infra.graph.report_generator import ReportGenerator, SECTIONS as REPORT_SECTIONS
from app.infra.graph.entity_extractor import EntityExtractor

logger = logging.getLogger(__name__)


class RunComplianceReportUseCase:
    def __init__(
        self,
        run_search: RunSearchUseCase,
        neo4j: Neo4jClient,
        hf_token: str = "",
    ):
        self.run_search = run_search
        self.neo4j = neo4j
        self.hf_token = hf_token
        self.extractor = EntityExtractor(hf_token)
        self.builder = GraphBuilder(neo4j, self.extractor)
        self.retriever = RagRetriever(neo4j)
        self.generator = ReportGenerator(hf_token)

    async def execute(self, query: str) -> ComplianceReport:
        logger.info(f"[Compliance] Iniciando relatório para: {query!r}")

        # Garante que o schema Neo4j existe
        await setup_schema(self.neo4j)

        # 1. Busca — usa o pipeline existente
        search_response = await self.run_search.execute(query)
        logger.info(
            f"[Compliance] Busca concluída: {len(search_response.results)} resultados, "
            f"{len(search_response.process_entities)} processos"
        )

        # 2. Constrói/atualiza o grafo
        stats = await self.builder.build(query, search_response)
        target_id = _person_id(query)

        # 3. Gera cada seção do relatório
        sections: dict[str, ComplianceSection] = {}
        overall_risks = []

        for section_key, section_meta in REPORT_SECTIONS.items():
            logger.info(f"[Compliance] Gerando seção: {section_meta['title']}")
            try:
                context = await self.retriever.retrieve_for_section(
                    section_key=section_key,
                    target_id=target_id,
                    semantic_query=section_meta["semantic_query"],
                )

                content, risk_level = await self.generator.generate_section(
                    section_key=section_key,
                    target=query,
                    graph_data=context["graph_data"],
                    chunks=context["chunks"],
                )

                sources = list({
                    c.get("source_url", "") for c in context["chunks"] if c.get("source_url")
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
                logger.error(f"[Compliance] Seção '{section_key}' falhou: {e}")
                sections[section_key] = ComplianceSection(
                    title=REPORT_SECTIONS[section_key]["title"],
                    content="Erro ao gerar esta seção.",
                    risk_level="N/A",
                )

        # Calcula risco geral
        risk_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "N/A": 0}
        overall = max(overall_risks, key=lambda r: risk_order.get(r, 0), default="N/A")

        # Stats do grafo após construção
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
        )
