import logging
from typing import Optional

import httpx


logger = logging.getLogger(__name__)


class FlareSolverrClient:

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def get_page(self, url: str, proxy_url: Optional[str] = None) -> str:
        api_url = f"{self.base_url}/v1"

        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": 60000,
        }

        if proxy_url:
            payload["proxy"] = proxy_url

        try:
            async with httpx.AsyncClient(timeout=75.0) as client:
                logger.info(f"[FlareSolverr] Resolving url={url}")

                response = await client.post(
                    api_url,
                    json=payload,
                )

                response.raise_for_status()

                data = response.json()

                if data.get("status") != "ok":
                    logger.warning(
                        f"[FlareSolverr FAIL] url={url} "
                        f"message={data.get('message')}"
                    )
                    return ""

                solution = data.get("solution", {})
                html = solution.get("response", "")

                logger.info(
                    f"[FlareSolverr OK] url={url} html_size={len(html)}"
                )

                return html

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[FlareSolverr HTTP ERROR] url={url} "
                f"status={e.response.status_code}"
            )
            return ""

        except httpx.RequestError as e:
            logger.error(
                f"[FlareSolverr CONNECTION ERROR] url={url} error={str(e)}"
            )
            return ""

        except Exception as e:
            logger.error(
                f"[FlareSolverr UNKNOWN ERROR] url={url} error={str(e)}"
            )
            return ""