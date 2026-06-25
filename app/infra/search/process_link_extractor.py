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
    "/processos/nome/",
    "assets.",
    "static.", 
    ]

    ALLOWED_PATTERNS = [
        "/processos-judiciais/",
        "/processos/",
        "/diarios/",
    ]

    @classmethod
    def extract(cls, html: str, base_url: str) -> list[str]:
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")

        links = []

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            full_url = urljoin(base_url, href)

            if cls._is_valid_process_link(full_url):
                links.append(full_url)

        return cls._deduplicate(links)

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