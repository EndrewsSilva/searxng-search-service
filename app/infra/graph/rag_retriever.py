"""
RAG Retriever: combina travessia de grafo (Cypher) + busca vetorial semântica.
"""
import logging
from app.infra.graph.neo4j_client import Neo4jClient
from app.infra.graph import embeddings as emb

logger = logging.getLogger(__name__)

VECTOR_K = 8


class RagRetriever:
    def __init__(self, neo4j: Neo4jClient):
        self.neo4j = neo4j

    async def retrieve_for_section(self, section_key: str, target_id: str, semantic_query: str) -> dict:
        graph_data = await self._graph_query(section_key, target_id)
        chunks = await self._vector_search(semantic_query, target_id)
        return {"graph_data": graph_data, "chunks": chunks}

    async def _graph_query(self, section_key: str, target_id: str) -> list[dict]:
        query_fn = SECTION_QUERIES.get(section_key)
        if not query_fn:
            return []
        try:
            cypher = query_fn(target_id)  # call the function to get the Cypher string
            return await self.neo4j.run(cypher, target_id=target_id)
        except Exception as e:
            logger.warning(f"[RAG] graph query '{section_key}' falhou: {e}")
            return []

    async def _vector_search(self, semantic_query: str, target_id: str) -> list[dict]:
        try:
            query_vector = await emb.embed_one_async(semantic_query)
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

    async def get_all_source_urls(self, target_id: str) -> list[str]:
        """Retorna todas as URLs fonte dos chunks associados ao alvo."""
        try:
            rows = await self.neo4j.run(
                """
                MATCH (c:Chunk)-[:ABOUT]->(p:Person {id: $target_id})
                WHERE c.source_url IS NOT NULL AND c.source_url <> ''
                RETURN DISTINCT c.source_url AS url
                ORDER BY url
                """,
                target_id=target_id,
            )
            return [r["url"] for r in rows if r.get("url")]
        except Exception as e:
            logger.warning(f"[RAG] get_all_source_urls falhou: {e}")
            return []


# --------------------------------------------------------------------------
# Queries Cypher por seção
# --------------------------------------------------------------------------

def _query_sumario(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})
    OPTIONAL MATCH (p)-[r1:WORKS_AT|IS_SOCIO]->(c:Company)
    OPTIONAL MATCH (p)-[:PARTICIPATES_IN]->(proc:LegalProcess)
    RETURN
      p.name AS nome,
      p.occupation AS ocupacao,
      count(DISTINCT c)    AS total_empresas,
      count(DISTINCT proc) AS total_processos,
      collect(DISTINCT {empresa: c.name, cnpj: c.cnpj, papel: type(r1)}) AS empresas
    """


def _query_identificacao(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})
    OPTIONAL MATCH (p)-[r1:WORKS_AT|IS_SOCIO]->(c:Company)
    OPTIONAL MATCH (p)-[r2:RELATED_TO]->(rel:Person)
    RETURN
      p.name AS nome,
      p.occupation AS ocupacao,
      p.age AS idade,
      p.city AS cidade,
      collect(DISTINCT {empresa: c.name, cnpj: c.cnpj, papel: type(r1)}) AS empresas,
      collect(DISTINCT {nome: rel.name, papel: type(r2)}) AS relacionados
    """


def _query_participacoes(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})-[r:IS_SOCIO|WORKS_AT]->(c:Company)
    RETURN
      c.name   AS empresa,
      c.cnpj   AS cnpj,
      c.sector AS setor,
      type(r)  AS papel,
      c.source_url AS fonte
    ORDER BY c.name
    """


def _query_vinculos(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})-[r:IS_SOCIO|WORKS_AT]->(c:Company)
    RETURN
      c.name     AS empresa,
      c.cnpj     AS cnpj,
      c.sector   AS setor,
      type(r)    AS papel,
      r.role     AS cargo,
      r.since    AS desde,
      c.source_url AS fonte
    ORDER BY r.since DESC
    """


def _query_producao(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})
    OPTIONAL MATCH (p)-[:MENTIONED_IN]->(e:Event)
    WHERE e.type IN ['publicacao', 'premio', 'reconhecimento', 'dissertacao']
    RETURN e.name AS evento, e.type AS tipo, e.date AS data, e.source_url AS fonte
    ORDER BY e.date DESC
    """


def _query_processos(target_id: str) -> str:
    return """
    MATCH (p:Person {id: $target_id})-[rel:PARTICIPATES_IN]->(proc:LegalProcess)
    RETURN
      proc.cnj_number  AS cnj,
      proc.class_      AS classe,
      proc.status      AS status,
      proc.subject     AS assunto,
      proc.court       AS tribunal,
      proc.origin_date AS data_inicio,
      proc.source_url  AS fonte,
      rel.role         AS polo
    ORDER BY proc.origin_date DESC
    LIMIT 20
    """


SECTION_QUERIES = {
    "sumario_executivo":        _query_sumario,
    "identificacao_contexto":   _query_identificacao,
    "participacoes_societarias": _query_participacoes,
    "vinculos_profissionais":   _query_vinculos,
    "producao_academica":       _query_producao,
    "processos_alertas":        _query_processos,
}

SECTIONS = SECTION_QUERIES
