import asyncio
import logging
import random
import re
import unicodedata
from urllib.parse import urlparse, quote

from typing import Optional

from app.domain.models.search import SearchResponse, SearchResult, ProcessEntity
from app.infra.search.html_parser import HTMLContentParser
from app.infra.search.process_entity_extractor import ProcessEntityExtractor
from app.infra.search.process_link_extractor import ProcessLinkExtractor
from app.infra.utils.detection import AntiBotDetector
from app.infra.utils.resilience import DomainLimiter, CircuitBreaker


logger = logging.getLogger(__name__)


class RunSearchUseCase:

    LEGAL_DOMAINS = [
        "jusbrasil.com.br",
        "escavador.com",
        "econodata.com.br",
    ]

    OPEN_WEB_TERMS = [
        "processo judicial",
        "CNPJ empresa sócio",
        "fraude corrupção sanções",
        "mídia negativa PEP",
    ]

    MAX_RESULTS = 25
    MIN_SEARCH_DELAY = 3.0
    MAX_SEARCH_DELAY = 7.0

    def __init__(self, search_client, flaresolverr_client, datajud_client=None):
        self.search_client = search_client
        self.flaresolverr_client = flaresolverr_client
        self.datajud_client = datajud_client

        self.flaresolverr_semaphore = asyncio.Semaphore(3)
        self.domain_limiter = DomainLimiter(delay=2.5)
        self.breaker = CircuitBreaker(threshold=3, cooldown=60)

        self._inflight: dict[str, asyncio.Task] = {}
        self._inflight_lock = asyncio.Lock()

    async def execute(self, query: str):
        try:
            datajud_coro = (
                self.datajud_client.search_by_name(query)
                if self.datajud_client
                else asyncio.sleep(0)
            )

            search_results, legal_results, datajud_raw = await asyncio.gather(
                self._search_all_strategies(query),
                self._direct_legal_discovery(query),
                datajud_coro,
                return_exceptions=True,
            )

            raw_results = []
            for r in [search_results, legal_results]:
                if isinstance(r, Exception):
                    logger.error(f"[SEARCH FAIL] {str(r)}")
                else:
                    raw_results.extend(r)

            datajud_entities: list[ProcessEntity] = []
            if self.datajud_client and not isinstance(datajud_raw, Exception) and datajud_raw:
                datajud_entities = datajud_raw
                print(f"\n[DataJud] {len(datajud_entities)} processos encontrados via API CNJ")

        except Exception as e:
            logger.error(f"[SEARCH FAIL] Falha ao buscar: {str(e)}")
            return SearchResponse(query=query, total_found=0, results=[], process_entities=[])

        total_before_dedup = len(raw_results)
        raw_results = self._deduplicate_by_url(raw_results)
        total_after_dedup = len(raw_results)

        ranked_results = self._rank_results_for_scraping(raw_results, query)

        selected_results = [
            item
            for item in ranked_results
            if self._pre_scrap_score(item, query) > 0
        ][:self.MAX_RESULTS]

        print("\n" + "=" * 100)
        print("[PIPELINE SUMMARY]")
        print("TOTAL FONTES ENCONTRADAS ANTES DO DEDUP:", total_before_dedup)
        print("TOTAL FONTES ÚNICAS APÓS DEDUP:", total_after_dedup)
        print("TOTAL FONTES SELECIONADAS PARA SCRAPING:", len(selected_results))
        print("=" * 100 + "\n")

        print("[SELECTED FOR SCRAPING]")
        for i, item in enumerate(selected_results, 1):
            print(
                f"{i:02d}. rank={self._pre_scrap_score(item, query):03d} "
                f"domain={self._extract_domain(item.get('url', ''))} "
                f"title={item.get('title', '')}"
            )
        print()

        if not selected_results:
            return SearchResponse(query=query, total_found=0, results=[], process_entities=[])

        tasks = [
            self._deduplicated_fetch(item.get("url", ""))
            for item in selected_results
        ]

        html_pages = await asyncio.gather(*tasks, return_exceptions=True)

        successful_scraps = 0
        failed_scraps = 0
        blocked_scraps = 0

        for page in html_pages:
            if isinstance(page, Exception) or not page:
                failed_scraps += 1
            elif AntiBotDetector.is_blocked(page):
                blocked_scraps += 1
            else:
                successful_scraps += 1

        print("\n" + "=" * 100)
        print("[SCRAP SUMMARY]")
        print("SCRAPS TENTADOS:", len(html_pages))
        print("SCRAPS COM SUCESSO:", successful_scraps)
        print("SCRAPS BLOQUEADOS:", blocked_scraps)
        print("SCRAPS FALHARAM:", failed_scraps)
        print("=" * 100 + "\n")

        results = []
        process_entities = []
        extracted_process_candidates = 0
        accepted_process_entities = 0

        for item, html_content in zip(selected_results, html_pages):
            url = item.get("url", "")
            domain = self._extract_domain(url)

            html_raw = ""
            deep_content = ""

            if (
                isinstance(html_content, Exception)
                or not html_content
                or AntiBotDetector.is_blocked(html_content)
            ):
                deep_content = (
                    "Conteúdo temporariamente indisponível "
                    "por mitigações de segurança do portal."
                )
            else:
                html_raw = html_content
                deep_content = HTMLContentParser.clean_html_to_text(html_raw)

            score, score_reasons = self._score_result(
                item=item,
                query=query,
                html_text=deep_content,
            )

            process_links = ProcessLinkExtractor.extract(html_raw, url)

            if html_raw:
                try:
                    entity_items = ProcessEntityExtractor.extract_many(html_raw, url)
                    extracted_process_candidates += len(entity_items)

                    for entity_data in entity_items:
                        entity = ProcessEntity(**entity_data)

                        if self._is_valid_process_entity(entity, query):
                            process_entities.append(entity)
                            accepted_process_entities += 1

                except Exception as e:
                    logger.error(f"[ProcessEntityExtractor FAIL] url={url} erro={str(e)}")

            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=url,
                    snippet=item.get("content", ""),
                    domain=domain,
                    html_full=deep_content,
                    html_raw=None,
                    is_exact_match=score >= 70,
                    matched_name=query if score >= 70 else None,
                    score=score,
                    score_reasons=score_reasons,
                    process_links=process_links,
                )
            )

        results = sorted(results, key=lambda result: result.score or 0, reverse=True)

        scraped_entities = self._deduplicate_process_entities(process_entities)
        all_process_entities = self._deduplicate_process_entities(
            scraped_entities + datajud_entities
        )

        print("\n" + "=" * 100)
        print("[PROCESS EXTRACTION SUMMARY]")
        print("CANDIDATOS DE PROCESSO EXTRAÍDOS (scraping):", extracted_process_candidates)
        print("PROCESSOS ACEITOS (scraping):", len(scraped_entities))
        print("PROCESSOS VIA DATAJUD (API CNJ):", len(datajud_entities))
        print("PROCESSOS ÚNICOS APÓS MERGE + DEDUP:", len(all_process_entities))
        print("=" * 100 + "\n")

        print("\n" + "=" * 100)
        print("[FINAL SUMMARY]")
        print("QUERY:", query)
        print("RESULTADOS RETORNADOS NO JSON:", len(results))
        print("PROCESSOS ESTRUTURADOS:", len(all_process_entities))
        print("=" * 100 + "\n")

        return SearchResponse(
            query=query,
            total_found=len(results),
            results=results,
            process_entities=all_process_entities,
        )

    async def _direct_legal_discovery(self, query: str) -> list[dict]:
        """Scrapa as páginas de busca dos sites jurídicos diretamente via FlareSolverr,
        sem depender de motores de busca. Extrai links de processos encontrados."""
        query_encoded = quote(query)
        search_pages = [
            f"https://www.jusbrasil.com.br/busca?q={query_encoded}",
            f"https://www.escavador.com/busca?q={query_encoded}",
        ]

        print("\n" + "=" * 100)
        print("[DIRECT LEGAL DISCOVERY] Acessando sites jurídicos diretamente via FlareSolverr")
        for url in search_pages:
            print(f"  → {url}")
        print("=" * 100 + "\n")

        htmls = await asyncio.gather(
            *[self._fetch_with_resilience(url) for url in search_pages],
            return_exceptions=True,
        )

        results = []
        for url, html in zip(search_pages, htmls):
            domain = self._extract_domain(url)

            if isinstance(html, Exception) or not html or AntiBotDetector.is_blocked(html):
                logger.warning(f"[DirectLegalDiscovery] Bloqueado ou falha: {url}")
                print(f"[DIRECT LEGAL DISCOVERY] FALHA → {domain}")
                continue

            links = ProcessLinkExtractor.extract(html, url)
            print(f"[DIRECT LEGAL DISCOVERY] OK → {domain} | {len(links)} process links extraídos")

            for link in links:
                results.append({
                    "url": link,
                    "title": f"{query} - {domain}",
                    "content": "",
                    "engine": "direct",
                    "engines": ["direct"],
                })

        return results

    async def _search_all_strategies(self, query: str) -> list[dict]:
        all_results = []
        engines_used = set()
        sources_by_engine = {}
        sources_by_domain = {}
        sources_by_query = {}

        open_queries = [{"type": "open_web", "query": f'"{query}"'}]

        for term in self.OPEN_WEB_TERMS:
            open_queries.append(
                {"type": "open_web_term", "query": f'"{query}" {term}'}
            )

        random.shuffle(open_queries)
        search_queries = open_queries

        print("\n" + "=" * 100)
        print("[SEARCH START] - BUSCA AMPLA NEUTRA COM FILTRO DE QUALIDADE")
        print("QUERY BASE:", query)
        print("TOTAL DE CONSULTAS GERADAS:", len(search_queries))
        print("=" * 100)

        for idx, search_item in enumerate(search_queries):
            search_query = search_item["query"]
            search_type = search_item["type"]

            if idx > 0:
                sleep_time = random.uniform(self.MIN_SEARCH_DELAY, self.MAX_SEARCH_DELAY)
                print(f"[ANTI-CAPTCHA] Aguardando {sleep_time:.2f}s antes da próxima consulta...")
                await asyncio.sleep(sleep_time)

            try:
                raw_data = await self.search_client.search(search_query)
                domain_results = raw_data.get("results", [])
                sources_by_query[search_query] = len(domain_results)

                print(
                    f"[SEARCH QUERY RESULT] "
                    f"type={search_type} "
                    f"found={len(domain_results)} "
                    f"query={search_query}"
                )

                for result in domain_results:
                    url = result.get("url", "")
                    domain = self._extract_domain(url)

                    if domain:
                        sources_by_domain[domain] = sources_by_domain.get(domain, 0) + 1

                    engine = result.get("engine")
                    engines = result.get("engines", [])

                    if engine:
                        engines_used.add(engine)
                        sources_by_engine[engine] = sources_by_engine.get(engine, 0) + 1

                    for item_engine in engines:
                        engines_used.add(item_engine)
                        sources_by_engine[item_engine] = (
                            sources_by_engine.get(item_engine, 0) + 1
                        )

                all_results.extend(domain_results)

            except Exception as e:
                logger.error(
                    "[SEARCH QUERY FAIL] type=%s query=%s error=%s",
                    search_type,
                    search_query,
                    str(e),
                )

        print("\n" + "=" * 100)
        print("[SEARCH SUMMARY]")
        print("TOTAL FONTES ENCONTRADAS ANTES DO DEDUP:", len(all_results))
        print("MOTORES DE BUSCA UTILIZADOS:", sorted(engines_used))

        print("FONTES POR MOTOR:")
        for engine, count in sorted(
            sources_by_engine.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            print(f"  {engine}: {count}")

        print("FONTES POR DOMÍNIO:")
        for domain, count in sorted(
            sources_by_domain.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            print(f"  {domain}: {count}")

        print("\nFONTES POR CONSULTA:")
        for q, count in sources_by_query.items():
            print(f"  {count:03d} | {q}")

        print("=" * 100 + "\n")

        return all_results

    def _rank_results_for_scraping(self, results: list[dict], query: str) -> list[dict]:
        return sorted(
            results,
            key=lambda item: self._pre_scrap_score(item, query),
            reverse=True,
        )

    def _pre_scrap_score(self, item: dict, query: str) -> int:
        score = 0

        title = self._normalize_text(item.get("title", ""))
        snippet = self._normalize_text(item.get("content", ""))
        url = item.get("url", "")
        url_norm = self._normalize_text(url)
        domain = self._extract_domain(url)

        query_norm = self._normalize_text(query)
        query_slug = self._slugify(query)

        text_blob = f"{title} {snippet} {url_norm}"

        has_exact_name = query_norm in text_blob
        has_query_slug = query_slug in self._slugify(url)

        if self._is_specific_process_url(url):
            score += 220

        if domain in ["jusbrasil.com.br", "escavador.com"] and (
            has_exact_name or has_query_slug
        ):
            score += 220

        if domain == "econodata.com.br" and has_exact_name:
            score += 120

        if domain == "econodata.com.br" and not has_exact_name:
            score -= 200

        if domain in self.LEGAL_DOMAINS and not (has_exact_name or has_query_slug):
            score -= 120

        if (
            (domain.endswith(".gov.br") or domain == "gov.br")
            and (has_exact_name or has_query_slug)
        ):
            score += 80

        if title.startswith(query_norm):
            score += 90

        if has_query_slug:
            score += 80

        if query_norm in snippet:
            score += 60

        if any(
            token in url_norm
            for token in [
                "processo",
                "processos",
                "processos-judiciais",
                "diarios",
                "jurisprudencia",
            ]
        ):
            score += 70

        if any(
            token in text_blob
            for token in [
                "sanção",
                "sancao",
                "pep",
                "corrupção",
                "corrupcao",
                "fraude",
                "lavagem",
                "cnpj",
                "empresa",
                "sócio",
                "socio",
            ]
        ):
            score += 35

        generic_legal_paths = [
            "/diarios",
            "/jurisprudencia",
            "/jurisprudencias",
            "/relatorios-juridicos",
            "/consulta",
            "/consulta-processual",
            "/acompanhamento-processual",
            "/solucoes",
            "/business",
            "/login",
        ]

        if domain in self.LEGAL_DOMAINS and any(path in url_norm for path in generic_legal_paths):
            score -= 140

        if any(
            bad in url_norm
            for bad in [
                "/processos/nome/",
                "/busca?",
                "/nomes/",
                "/sobre/",
            ]
        ):
            score -= 80

        if self._is_likely_homonym_or_related_person(item, query):
            score -= 300

        noisy_domains = [
            "bibliaonline.com.br",
            "bibliaon.com",
            "bibliaportugues.com",
            "oracaoefe.com.br",
            "dicionariodenomesproprios.com.br",
            "youtube.com",
            "wikipedia.org",
            "pt.wikipedia.org",
            "en.wikipedia.org",
            "gazetadigital.com.br",
            "jaobrasil.com",
            "joaobidu.com.br",
            "teologointernacional.com.br",
            "g1.globo.com",
            "folhavitoria.com.br",
            "vaticannews.va",
            "exame.com",
            "dol.com.br",
            "facebook.com",
        ]

        if any(domain == d or domain.endswith("." + d) for d in noisy_domains):
            score -= 300

        return score

    def _is_likely_homonym_or_related_person(self, item: dict, query: str) -> bool:
        title = self._normalize_text(item.get("title", ""))
        snippet = self._normalize_text(item.get("content", ""))
        url = item.get("url", "")
        domain = self._extract_domain(url)

        query_norm = self._normalize_text(query)
        query_tokens = self._name_tokens(query)

        title_matches = sum(1 for token in query_tokens if token in title)
        title_ratio = title_matches / max(1, len(query_tokens))

        is_specific_process = self._is_specific_process_url(url)

        if is_specific_process:
            return False

        if domain in {"jusbrasil.com.br", "escavador.com", "econodata.com.br"}:
            if query_norm not in title and title_ratio < 0.75:
                return True

        if domain == "econodata.com.br" and query_norm not in title:
            return True

        if domain == "facebook.com":
            return True

        if "processos" in title and query_norm not in title and title_ratio < 0.75:
            return True

        return False

    def _name_tokens(self, text: str) -> list[str]:
        normalized = self._normalize_text(text)

        stop_tokens = {
            "de",
            "da",
            "do",
            "das",
            "dos",
            "e",
        }

        return [
            token
            for token in normalized.split()
            if len(token) > 2 and token not in stop_tokens
        ]

    def _deduplicate_by_url(self, results: list[dict]) -> list[dict]:
        seen = set()
        unique = []

        for item in results:
            url = item.get("url")

            if not url:
                continue

            normalized_url = url.strip().rstrip("/")

            if normalized_url in seen:
                continue

            seen.add(normalized_url)
            unique.append(item)

        return unique

    def _is_valid_process_entity(self, entity: ProcessEntity, query: str) -> bool:
        query_norm = self._normalize_text(query)

        title_norm = self._normalize_text(entity.title)
        description_norm = self._normalize_text(entity.description)
        preview_norm = self._normalize_text(entity.text_preview or "")
        source_url = entity.source_url or ""

        text_blob = f"{title_norm} {description_norm} {preview_norm}"

        if "nenhum processo encontrado" in text_blob:
            return False

        if "0 processos" in text_blob:
            return False

        query_tokens = self._name_tokens(query)

        matched_tokens = sum(
            1
            for token in query_tokens
            if token in text_blob
        )

        if matched_tokens < 3:
            return False

        if matched_tokens < max(3, len(query_tokens) // 2):
            return False

        if entity.has_process_number:
            return True

        if self._is_specific_process_url(source_url) and bool(entity.parties):
            return True

        return False

    def _deduplicate_process_entities(self, entities: list[ProcessEntity]) -> list[ProcessEntity]:
        seen = set()
        unique = []

        for entity in entities:
            if entity.process_numbers:
                key = "|".join(entity.process_numbers)
            else:
                key = entity.source_url.strip().rstrip("/")

            if key in seen:
                continue

            seen.add(key)
            unique.append(entity)

        return unique

    def _score_result(self, item: dict, query: str, html_text: str) -> tuple[int, list[str]]:
        score = 0
        reasons = []

        title = self._normalize_text(item.get("title", ""))
        snippet = self._normalize_text(item.get("content", ""))
        url = item.get("url", "")
        url_norm = self._normalize_text(url)
        html_norm = self._normalize_text(html_text)

        query_norm = self._normalize_text(query)
        query_slug = self._slugify(query)

        domain = self._extract_domain(url)

        if domain in self.LEGAL_DOMAINS:
            score += 20
            reasons.append(f"Domínio jurídico permitido: {domain}")

        if self._is_specific_process_url(url):
            score += 30
            reasons.append("URL específica de processo")

        if title.startswith(query_norm):
            score += 35
            reasons.append("Título inicia com o nome consultado")

        if query_slug in self._slugify(url):
            score += 25
            reasons.append("URL contém o slug do nome consultado")

        if query_norm in snippet:
            score += 15
            reasons.append("Snippet contém o nome consultado")

        if query_norm in html_norm:
            score += 15
            reasons.append("Conteúdo raspado contém o nome consultado")

        if any(
            token in url_norm
            for token in [
                "processo",
                "processos",
                "diarios",
                "jurisprudencia",
            ]
        ):
            score += 10
            reasons.append("Resultado jurídico/processual")

        if any(
            token in html_norm
            for token in [
                "sanção",
                "sancao",
                "pep",
                "corrupção",
                "corrupcao",
                "fraude",
                "lavagem de dinheiro",
                "mídia negativa",
                "midia negativa",
            ]
        ):
            score += 10
            reasons.append("Conteúdo contém termo de risco/compliance")

        if any(
            bad in url_norm
            for bad in [
                "/processos/nome/",
                "/busca?",
                "/nomes/",
                "/sobre/",
            ]
        ):
            score -= 60
            reasons.append("Página agregadora/busca/perfil penalizada")

        if "nenhum processo encontrado" in html_norm:
            score -= 30
            reasons.append("Página informa nenhum processo encontrado")

        if "nomes relacionados" in html_norm and not title.startswith(query_norm):
            score -= 25
            reasons.append("Nome aparece apenas como relacionado")

        return max(score, 0), reasons

    @staticmethod
    def _is_specific_process_url(url: str) -> bool:
        url = url or ""

        return (
            (
                "/processos/" in url
                and "/processos/nome/" not in url
            )
            or "/processos-judiciais/" in url
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text or ""
        text = text.lower()
        text = unicodedata.normalize("NFKD", text)
        text = "".join(
            char
            for char in text
            if not unicodedata.combining(char)
        )
        return " ".join(text.split())

    @staticmethod
    def _slugify(text: str) -> str:
        text = text or ""
        text = unicodedata.normalize("NFKD", text)
        text = "".join(
            char
            for char in text
            if not unicodedata.combining(char)
        )
        text = text.lower()
        text = re.sub(r"[^a-z0-9]+", "-", text)
        return text.strip("-")

    @staticmethod
    def _extract_domain(url: str) -> str:
        domain = urlparse(url).netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    async def _deduplicated_fetch(self, url: str) -> str:
        if not url:
            return ""

        async with self._inflight_lock:
            task = self._inflight.get(url)

            if task:
                return await task

            task = asyncio.create_task(self._fetch_with_resilience(url))
            self._inflight[url] = task

        try:
            return await task
        finally:
            async with self._inflight_lock:
                self._inflight.pop(url, None)

    async def _fetch_with_resilience(self, url: str) -> str:
        if not await self.breaker.can_execute():
            logger.warning(
                f"[CircuitBreaker OPEN] Raspagem suspensa temporariamente. Pulando: {url}"
            )
            return ""

        domain = self._extract_domain(url)
        await self.domain_limiter.wait(domain)

        async with self.flaresolverr_semaphore:
            try:
                html = await self.flaresolverr_client.get_page(url)

                if not html:
                    raise Exception("HTML vazio retornado pelo resolver.")

                if AntiBotDetector.is_blocked(html):
                    raise Exception("Bloqueio detectado no conteúdo da página.")

                await self.breaker.record_success()
                return html

            except Exception as e:
                logger.error(f"[Resilience Fetch FAIL] {url} -> {str(e)}")
                await self.breaker.record_failure()
                return ""