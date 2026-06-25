import logging

import httpx


logger = logging.getLogger(__name__)


class WhoogleBlockedError(Exception):
    pass


class WhoogleClient:

    def __init__(self, base_url: str = "http://localhost:5000"):
        self.base_url = base_url.rstrip("/")

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout=20.0,
                connect=5.0,
                read=20.0,
                write=5.0,
            ),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=5,
                keepalive_expiry=15.0,
            ),
            follow_redirects=True,
        )

    async def search(self, query: str, limit: int = 10) -> dict:
        params = {
            "q": query,
            "format": "json",
        }

        print("[WHOOGLE CLIENT]")
        print("QUERY:", query)

        try:
            response = await self.client.get(
                f"{self.base_url}/search",
                params=params,
            )

            response.raise_for_status()
            data = response.json()

            if data.get("blocked") is True:
                raise WhoogleBlockedError(
                    data.get("error_message", "Whoogle blocked or rate limited")
                )

            raw_results = data.get("results", [])[:limit]

            results = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("href", ""),
                    "content": item.get("content") or item.get("text", ""),
                    "engine": "whoogle",
                    "engines": ["whoogle"],
                }
                for item in raw_results
                if item.get("href")
            ]

            print("RESULTS:", len(results))

            return {
                "query": query,
                "results": results,
                "source": "whoogle",
            }

        except WhoogleBlockedError:
            raise

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[WHOOGLE HTTP ERROR] status={e.response.status_code} url={e.request.url}"
            )
            raise

        except httpx.RequestError as e:
            logger.error(f"[WHOOGLE CONNECTION ERROR] error={str(e)}")
            raise

    async def close(self):
        await self.client.aclose()