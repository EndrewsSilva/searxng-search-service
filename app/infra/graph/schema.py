"""
Cria constraints, índices full-text e índice vetorial no Neo4j.
Idempotente — pode ser chamado a cada startup.
"""
import logging
from app.infra.graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

CONSTRAINTS = [
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT company_id IF NOT EXISTS FOR (c:Company) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT process_cnj IF NOT EXISTS FOR (p:LegalProcess) REQUIRE p.cnj_number IS UNIQUE",
    "CREATE CONSTRAINT event_id IF NOT EXISTS FOR (e:Event) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT org_id IF NOT EXISTS FOR (o:Organization) REQUIRE o.id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
]

FULLTEXT_INDEXES = [
    """
    CREATE FULLTEXT INDEX entity_name IF NOT EXISTS
    FOR (n:Person|Company|Organization) ON EACH [n.name]
    """,
]

VECTOR_INDEX = """
CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
FOR (c:Chunk) ON (c.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 384, `vector.similarity_function`: 'cosine'}}
"""


async def setup_schema(client: Neo4jClient):
    for stmt in CONSTRAINTS:
        try:
            await client.run(stmt)
        except Exception as e:
            logger.debug(f"[Schema] constraint skip: {e}")

    for stmt in FULLTEXT_INDEXES:
        try:
            await client.run(stmt)
        except Exception as e:
            logger.debug(f"[Schema] fulltext skip: {e}")

    try:
        await client.run(VECTOR_INDEX)
    except Exception as e:
        logger.debug(f"[Schema] vector index skip: {e}")

    logger.info("[Schema] Neo4j schema pronto")
