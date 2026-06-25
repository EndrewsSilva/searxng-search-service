import logging

import httpx


logger = logging.getLogger(__name__)


class SearxngClient:

    DEFAULT_ENGINES = [
        "bing",
    ]

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

        limits = httpx.Limits(
            max_connections=50,
            max_keepalive_connections=10,
            keepalive_expiry=15.0,
        )

        timeout = httpx.Timeout(
            timeout=25.0,
            connect=5.0,
            read=25.0,
            write=5.0,
        )

        self.client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            follow_redirects=True,
        )

    async def search(self, query: str):
        params = {
            "q": query,
            "format": "json",
            "language": "pt-BR",
            "categories": "general",
            "engines": ",".join(self.DEFAULT_ENGINES),
        }

        try:
            logger.info(
                "[SearXNG] GET /search query=%s engines=%s",
                query,
                params["engines"],
            )

            response = await self.client.get(
                f"{self.base_url}/search",
                params=params,
            )

            response.raise_for_status()

            data = response.json()
            results = data.get("results", [])
            unresponsive = data.get("unresponsive_engines", [])

            logger.info(
                "[SearXNG] query=%s results=%s unresponsive_engines=%s",
                query,
                len(results),
                unresponsive,
            )

            print("[SearXNG CLIENT]")
            print("QUERY:", query)
            print("ENGINES REQUESTED:", params["engines"])
            print("RESULTS:", len(results))
            print("UNRESPONSIVE ENGINES:", unresponsive)

            return data

        except httpx.HTTPStatusError as e:
            logger.error(
                "[SearXNG HTTP ERROR] status=%s url=%s",
                e.response.status_code,
                e.request.url,
            )
            raise

        except httpx.RequestError as e:
            logger.error(
                "[SearXNG CONNECTION ERROR] error=%s",
                str(e),
            )
            raise

    async def close(self):
        await self.client.aclose()