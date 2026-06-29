"""
Constrói o grafo Neo4j a partir dos resultados de busca (SearchResponse).

Fluxo por busca:
  1. Cria nó Person para o alvo
  2. Para cada ProcessEntity → nó LegalProcess + aresta PARTICIPATES_IN
  3. Para cada SearchResult → chunking do html_full + embeddings + nós Chunk
  4. Para cada snippet/resultado → extração de entidades e relacionamentos
"""
import hashlib
import logging
import unicodedata

from app.domain.models.search import SearchResponse, ProcessEntity
from app.infra.graph.neo4j_client import Neo4jClient
from app.infra.graph.chunker import TextChunker
from app.infra.graph import embeddings as emb
from app.infra.graph.entity_extractor import EntityExtractor

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower().strip()


def _person_id(name: str) -> str:
    return hashlib.md5(_slugify(name).encode()).hexdigest()[:16]


def _company_id(name: str, cnpj: str = "") -> str:
    key = cnpj.strip() if cnpj else _slugify(name)
    return hashlib.md5(key.encode()).hexdigest()[:16]


class GraphBuilder:
    def __init__(
        self,
        neo4j: Neo4jClient,
        entity_extractor: EntityExtractor,
        chunk_size: int = 400,
    ):
        self.neo4j = neo4j
        self.extractor = entity_extractor
        self.chunker = TextChunker(chunk_size=chunk_size)

    async def build(self, query: str, response: SearchResponse) -> dict:
        """Constrói o grafo e retorna estatísticas."""
        stats = {"persons": 0, "companies": 0, "processes": 0, "events": 0, "chunks": 0, "relationships": 0}

        # 1. Nó do alvo principal
        target_id = _person_id(query)
        await self._merge_person(target_id, query)
        stats["persons"] += 1

        # 2. Processos judiciais estruturados
        for proc in response.process_entities:
            await self._add_process(target_id, proc)
            stats["processes"] += 1

        # 3. Chunks: coleta todos os textos primeiro, depois embeds em um batch
        all_chunks: list[dict] = []
        for result in response.results:
            text = result.html_full or result.snippet or ""
            if not text:
                continue
            chunks = self.chunker.chunk(text, source_url=result.url, source_type="web")
            all_chunks.extend(chunks)

        if all_chunks:
            all_texts = [c["text"] for c in all_chunks]
            vectors = await emb.embed_async(all_texts)
            for chunk, vector in zip(all_chunks, vectors):
                chunk["embedding"] = vector
                await self._merge_chunk(chunk, target_id)
            stats["chunks"] = len(all_chunks)

        # 4. Extração de entidades do snippet (rápido sem HF_TOKEN)
        for result in response.results:
            extracted = await self.extractor.extract(result.snippet or result.html_full or "")
            n, r = await self._integrate_extracted(target_id, extracted, result.url)
            stats["companies"] += n
            stats["relationships"] += r

        logger.info(f"[GraphBuilder] Grafo construído: {stats}")
        return stats

    async def _merge_person(self, pid: str, name: str):
        await self.neo4j.run_write(
            """
            MERGE (p:Person {id: $id})
            ON CREATE SET p.name = $name, p.created_at = datetime()
            ON MATCH  SET p.name = $name
            """,
            id=pid, name=name,
        )

    async def _add_process(self, target_id: str, proc: ProcessEntity):
        for cnj in (proc.process_numbers or []):
            if not cnj:
                continue
            await self.neo4j.run_write(
                """
                MERGE (p:LegalProcess {cnj_number: $cnj})
                ON CREATE SET
                  p.court    = $court,
                  p.class_   = $class_,
                  p.status   = $status,
                  p.subject  = $subject,
                  p.origin_date = $origin_date,
                  p.source_url  = $source_url
                WITH p
                MATCH (t:Person {id: $target_id})
                MERGE (t)-[r:PARTICIPATES_IN {cnj: $cnj}]->(p)
                ON CREATE SET r.role = $role
                """,
                cnj=cnj,
                court=proc.court or "",
                class_=proc.title or "",
                status=proc.status or "",
                subject=proc.subject or "",
                origin_date=proc.origin_date or "",
                source_url=proc.source_url or "",
                target_id=target_id,
                role=self._infer_role(proc, target_id),
            )

    async def _merge_chunk(self, chunk: dict, target_id: str):
        await self.neo4j.run_write(
            """
            MERGE (c:Chunk {id: $id})
            ON CREATE SET
              c.text        = $text,
              c.embedding   = $embedding,
              c.source_url  = $source_url,
              c.source_type = $source_type
            WITH c
            MATCH (p:Person {id: $target_id})
            MERGE (c)-[:ABOUT]->(p)
            """,
            id=chunk["id"],
            text=chunk["text"],
            embedding=chunk["embedding"],
            source_url=chunk.get("source_url", ""),
            source_type=chunk.get("source_type", "web"),
            target_id=target_id,
        )

    async def _integrate_extracted(self, target_id: str, extracted: dict, source_url: str) -> tuple[int, int]:
        entities_by_name = {}
        new_nodes = 0
        new_rels = 0

        for ent in extracted.get("entities", []):
            name = (ent.get("name") or "").strip()
            etype = ent.get("type", "")
            attrs = ent.get("attributes", {})
            if not name or not etype:
                continue

            entities_by_name[name] = etype

            if etype == "Company":
                cid = _company_id(name, attrs.get("cnpj", ""))
                await self.neo4j.run_write(
                    """
                    MERGE (c:Company {id: $id})
                    ON CREATE SET c.name = $name, c.cnpj = $cnpj, c.sector = $sector, c.source_url = $src
                    """,
                    id=cid, name=name,
                    cnpj=attrs.get("cnpj", ""),
                    sector=attrs.get("sector", ""),
                    src=source_url,
                )
                new_nodes += 1

            elif etype == "Organization":
                oid = _person_id(name)
                await self.neo4j.run_write(
                    """
                    MERGE (o:Organization {id: $id})
                    ON CREATE SET o.name = $name, o.type = $type
                    """,
                    id=oid, name=name, type=attrs.get("type", ""),
                )
                new_nodes += 1

            elif etype == "Event":
                eid = _person_id(name)
                await self.neo4j.run_write(
                    """
                    MERGE (e:Event {id: $id})
                    ON CREATE SET e.name = $name, e.type = $type, e.date = $date, e.source_url = $src
                    """,
                    id=eid, name=name,
                    type=attrs.get("type", ""),
                    date=attrs.get("date", ""),
                    src=source_url,
                )
                new_nodes += 1

            elif etype == "Person" and name.lower() != _slugify(target_id):
                pid = _person_id(name)
                await self.neo4j.run_write(
                    """
                    MERGE (p:Person {id: $id})
                    ON CREATE SET p.name = $name, p.occupation = $occ
                    """,
                    id=pid, name=name, occ=attrs.get("occupation", ""),
                )

        # Relacionamentos
        for rel in extracted.get("relationships", []):
            from_name = (rel.get("from") or "").strip()
            to_name = (rel.get("to") or "").strip()
            rtype = rel.get("type", "RELATED_TO")
            attrs = rel.get("attributes", {})

            if not from_name or not to_name:
                continue

            from_id = _person_id(from_name)
            to_type = entities_by_name.get(to_name, "Person")
            to_id = _person_id(to_name) if to_type in ("Person",) else _company_id(to_name)

            try:
                await self.neo4j.run_write(
                    f"""
                    MATCH (a {{id: $from_id}})
                    MATCH (b {{id: $to_id}})
                    MERGE (a)-[r:{rtype}]->(b)
                    ON CREATE SET r.role = $role, r.since = $since
                    """,
                    from_id=from_id, to_id=to_id,
                    role=attrs.get("role", ""),
                    since=attrs.get("since", ""),
                )
                new_rels += 1
            except Exception:
                pass  # IDs não encontrados no grafo

        return new_nodes, new_rels

    @staticmethod
    def _infer_role(proc: ProcessEntity, target_id: str) -> str:
        parties = proc.parties or {}
        left = str(parties.get("left", "")).lower()
        right = str(parties.get("right", "")).lower()
        if left:
            return "requerente/autor"
        if right:
            return "requerido/réu"
        return "parte"
