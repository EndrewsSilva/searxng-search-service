"""
RAG Retriever: combina travessia de grafo (Cypher) + busca vetorial semântica.

Para cada seção do relatório de compliance:
  1. Executa query Cypher específica para dados estruturados do grafo
  2. Busca chunks semanticamente relevantes via índice vetorial
  3. Retorna contexto unificado para o LLM
"""
import logging
from app.infra.graph.neo4j_client import Neo4jClient
from app.infra.graph import embeddings as emb

logger = logging.getLogger(__name__)

VECTOR_K = 6  # chunks mais próximos por query semântica


class RagRetriever:
    def __init__(self, neo4j: Neo4jClient):
        self.neo4j = neo4j

    async def retrieve_for_section(self, section_key: str, target_id: str, semantic_query: str) -> dict:
        """Retorna {graph_data, chunks} para uma seção específica."""
        graph_data = await self._graph_query(section_key, target_id)
        chunks = await self._vector_search(semantic_query, target_id)
        return {"graph_data": graph_data, "chunks": chunks}

    async def _graph_query(self, section_key: str, target_id: str) -> list[dict]:
        query_fn = SECTION_QUERIES.get(section_key)
        if not query_fn:
            return []
        try:
            return await self.neo4j.run(query_fn, target_id=target_id)
        except Exception as e:
            logger.warning(f"[RAG] graph query '{section_key}' falhou: {e}")
            return []

    async def _vector_search(self, semantic_query: str, target_id: str) -> list[dict]:
        try:
            query_vector = emb.embed_one(semantic_query)
            if not query_vector:
                return []

            rows = await self.neo4j.run(
                """
                CALL db.index.vector.queryNodes('chunk_embedding', $k, $embedding)
                YIELD node AS c, score
                MATCH (c)-[:ABOUT]->(p:Person {id: $target_id})
                RETURN c.text AS text, c.source_url AS source_url, score
                ORDER BY score DESC
                """,
                k=VECTOR_K,
                embedding=query_vector,
                target_id=target_id,
            )
            return rows
        except Exception as e:
            logger.warning(f"[RAG] vector search falhou: {e}")
            return []

    async def get_graph_stats(self, target_id: str) -> dict:
        try:
            rows = await self.neo4j.run(
                """
                MATCH (p:Person {id: $target_id})
                OPTIONAL MATCH (p)-[:PARTICIPATES_IN]->(proc:LegalProcess)
                OPTIONAL MATCH (p)-[:IS_SOCIO|WORKS_AT]->(c:Company)
                OPTIONAL MATCH (p)-[:RELATED_TO]->(r:Person)
                OPTIONAL MATCH (p)-[:MENTIONED_IN]->(e:Event)
                OPTIONAL MATCH (chunk:Chunk)-[:ABOUT]->(p)
                RETURN
                  count(DISTINCT proc) AS processes,
                  count(DISTINCT c)    AS companies,
                  count(DISTINCT r)    AS related_persons,
                  count(DISTINCT e)    AS events,
                  count(DISTINCT chunk) AS chunks
                """,
                target_id=target_id,
            )
            return rows[0] if rows else {}
        except Exception as e:
            logger.warning(f"[RAG] stats query falhou: {e}")
            return {}


# --------------------------------------------------------------------------
# Queries Cypher por seção
# --------------------------------------------------------------------------

def _query_identificacao(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})
    OPTIONAL MATCH (p)-[r1:WORKS_AT|IS_SOCIO]->(c:Company)
    OPTIONAL MATCH (p)-[r2:RELATED_TO]->(rel:Person)
    RETURN
      p.name AS nome,
      p.occupation AS ocupacao,
      collect(DISTINCT {empresa: c.name, cnpj: c.cnpj, setor: c.sector, papel: type(r1)}) AS empresas,
      collect(DISTINCT {nome: rel.name, papel: type(r2)}) AS relacionados
    """


def _query_processos(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})-[rel:PARTICIPATES_IN]->(proc:LegalProcess)
    RETURN
      proc.cnj_number AS cnj,
      proc.class_     AS classe,
      proc.status     AS status,
      proc.subject    AS assunto,
      proc.court      AS tribunal,
      proc.origin_date AS data_inicio,
      proc.source_url  AS fonte,
      rel.role         AS polo
    ORDER BY proc.origin_date DESC
    """


def _query_compliance(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})-[:MENTIONED_IN]->(e:Event)
    RETURN e.name AS evento, e.type AS tipo, e.date AS data, e.source_url AS fonte
    ORDER BY e.date DESC
    """


def _query_entidades(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})-[:MEMBER_OF]->(o:Organization)
    RETURN o.name AS nome, o.type AS tipo
    """


def _query_ramos(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})-[:IS_SOCIO|WORKS_AT]->(c:Company)
    RETURN c.name AS empresa, c.sector AS setor, c.cnpj AS cnpj
    """


def _query_risco(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})-[:PARTICIPATES_IN|MENTIONED_IN]->(n)
    WHERE n.type IN ['crypto', 'bet', 'gambling', 'shell', 'offshore']
    RETURN n.name AS nome, n.type AS tipo, labels(n)[0] AS label
    """


SECTION_QUERIES = {
    "identificacao_perfil": _query_identificacao,
    "processos_judiciais": _query_processos,
    "compliance_integridade": _query_compliance,
    "entidades_organizacoes": _query_entidades,
    "ramos_sensiveis": _query_ramos,
    "atividades_risco": _query_risco,
}

SECTIONS = SECTION_QUERIES
