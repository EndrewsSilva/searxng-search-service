import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


class ProcessLinkExtractor:

    BLOCKED_PARTS = [
        "/login",
        "/cadastrar",
        "/solucoes",
        "monitoramentos",
        "escavai",
        "/consulta-processual/",
        "/diarios/",
        "/pecas/",
        "/modelos-pecas/",
        "/peca-",       # document pieces — premium paywall, no process numbers
        "/processos/nome/",
        "assets.",
        "static.",
    ]

    ALLOWED_PATTERNS = [
        "/processos-judiciais/",
        "/processos/",
        "/diarios/",
        "/nome/",
    ]

    @classmethod
    def extract(cls, html: str, base_url: str) -> list[str]:
        if not html:
            return []

        links = []

        # Tenta extrair do __NEXT_DATA__ do JusBrasil (Next.js/Apollo GraphQL)
        next_links = cls._extract_from_next_data(html)
        links.extend(next_links)

        # Extração clássica via tags <a href>
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            full_url = urljoin(base_url, href)
            if cls._is_valid_process_link(full_url):
                links.append(full_url)

        return cls._deduplicate(links)

    @classmethod
    def _extract_from_next_data(cls, html: str) -> list[str]:
        """
        Extrai URLs de entidades (pessoas/empresas) e processos do __NEXT_DATA__
        do JusBrasil (estrutura Apollo GraphQL / Next.js SSR).
        """
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not m:
            return []

        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []

        page_props = data.get("props", {}).get("pageProps", {})
        apollo = page_props.get("__APOLLO_STATE__", {})
        if not apollo:
            return []

        links = []

        # Navega: ROOT_QUERY → root → searchHaystackSerp(...) → content.components
        root_data = apollo.get("ROOT_QUERY", {}).get("root", {})
        serp_key = next(
            (k for k in root_data if "searchHaystackSerp" in k), None
        )
        if serp_key:
            serp = root_data[serp_key]
            components = serp.get("content", {}).get("components", [])
            for group in components:
                for item in group.get("components", []):
                    fields = item.get("fields", {})
                    url = fields.get("url", "")
                    if not url:
                        continue

                    # Inclui apenas entidades com processos registrados
                    aggregations = fields.get("aggregations", {})
                    total_lawsuits = aggregations.get("total_lawsuits", 0)
                    lawsuit_count = fields.get("lawsuit_count", 0)
                    if total_lawsuits > 0 or lawsuit_count > 0:
                        links.append(url)

        # Também busca URLs de processo em qualquer chave do Apollo state
        _cnj_re = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
        for key, val in apollo.items():
            if not isinstance(val, dict):
                continue
            url = val.get("url", "")
            if url and "jusbrasil.com.br" in url:
                if "/processos/" in url and "/processos/nome/" not in url and "/peca-" not in url:
                    links.append(url)
                elif _cnj_re.search(url):
                    links.append(url)

        return links

    @classmethod
    def _is_valid_process_link(cls, url: str) -> bool:
        url_lower = url.lower()

        if any(blocked in url_lower for blocked in cls.BLOCKED_PARTS):
            return False

        domain = urlparse(url_lower).netloc

        if not (
            "escavador.com" in domain
            or "jusbrasil.com.br" in domain
        ):
            return False

        return any(pattern in url_lower for pattern in cls.ALLOWED_PATTERNS)

    @staticmethod
    def _deduplicate(links: list[str]) -> list[str]:
        seen = set()
        unique = []

        for link in links:
            if link in seen:
                continue

            seen.add(link)
            unique.append(link)

        return unique