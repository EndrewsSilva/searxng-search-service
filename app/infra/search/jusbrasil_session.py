import asyncio
import logging
from typing import Optional

import httpx


logger = logging.getLogger(__name__)


class JusBrasilSession:
    """
    Gerencia autenticação e sessão autenticada no JusBrasil.

    Fluxo de autenticação:
    1. FlareSolverr faz GET /login → obtém cf_clearance (bypass Cloudflare)
    2. Playwright injeta cf_clearance e navega a /login
    3. Playwright preenche email → clica Continuar → preenche senha → clica Login
    4. Extrai cookies da sessão autenticada do Playwright
    5. Páginas subsequentes: FlareSolverr com todos os cookies injetados

    Credenciais no .env:
        JUSBRASIL_EMAIL=seu@email.com
        JUSBRASIL_PASSWORD=suasenha
    """

    LOGIN_URL = "https://www.jusbrasil.com.br/login"

    def __init__(self, flaresolverr_url: str, email: str, password: str):
        self.flaresolverr_url = flaresolverr_url.rstrip("/")
        self.email = email
        self.password = password

        self._cookies: dict[str, str] = {}
        self._user_agent: str = ""
        self._lock = asyncio.Lock()
        self._authenticated = False

    async def authenticate(self) -> bool:
        async with self._lock:
            if self._authenticated:
                return True

            try:
                print("[JusBrasilSession] Iniciando autenticação via Playwright...")

                cf_cookies, ua = await self._get_cloudflare_clearance()
                print(f"[JusBrasilSession] cf_clearance obtido ({len(cf_cookies)} cookies)")

                session_cookies = await self._playwright_login(cf_cookies, ua)
                print(f"[JusBrasilSession] Login concluído. Cookies: {list(session_cookies.keys())}")

                self._cookies = session_cookies
                self._user_agent = ua
                self._authenticated = True

                logger.info("[JusBrasilSession] Autenticado com sucesso no JusBrasil")
                return True

            except Exception as e:
                logger.error(f"[JusBrasilSession] Falha na autenticação: {str(e)}")
                print(f"[JusBrasilSession] ERRO: {str(e)}")
                return False

    async def get_page(self, url: str) -> str:
        """Busca uma página do JusBrasil com sessão autenticada via FlareSolverr."""
        if not self._authenticated:
            success = await self.authenticate()
            if not success:
                logger.warning(f"[JusBrasilSession] Sem autenticação, tentando sem login: {url}")
                return await self._flaresolverr_get(url, cookies=[])

        return await self._flaresolverr_get(url, cookies=self._cookies_as_list())

    async def _get_cloudflare_clearance(self) -> tuple[dict[str, str], str]:
        """Usa FlareSolverr para obter cf_clearance (bypass Cloudflare)."""
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                f"{self.flaresolverr_url}/v1",
                json={
                    "cmd": "request.get",
                    "url": self.LOGIN_URL,
                    "maxTimeout": 30000,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "ok":
                raise Exception(f"FlareSolverr falhou: {data.get('message')}")

            sol = data["solution"]
            cookies = {c["name"]: c["value"] for c in sol.get("cookies", [])}
            ua = sol.get("userAgent", "")
            return cookies, ua

    async def _playwright_login(
        self, cf_cookies: dict[str, str], ua: str
    ) -> dict[str, str]:
        """
        Usa Playwright para preencher e submeter o formulário de login do JusBrasil.
        O cf_clearance já injetado permite que o Playwright bypasse o Cloudflare.
        """
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await browser.new_context(
                user_agent=ua,
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
            )

            # Injeta cf_clearance do FlareSolverr no contexto do Playwright
            pw_cookies = [
                {
                    "name": name,
                    "value": value,
                    "domain": ".jusbrasil.com.br",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "Lax",
                }
                for name, value in cf_cookies.items()
            ]
            await context.add_cookies(pw_cookies)

            page = await context.new_page()

            try:
                await page.goto(
                    self.LOGIN_URL,
                    timeout=30000,
                    wait_until="networkidle",
                )

                title = await page.title()
                if "momento" in title.lower() or "checking" in title.lower():
                    raise Exception(f"Cloudflare challenge não resolvido. Título: {title}")

                # Step 1: preenche email
                email_input = page.locator("input[type=email], input[name=email]").first
                await email_input.wait_for(timeout=10000)
                await email_input.fill(self.email)

                btn = page.locator("button[type=submit]").first
                await btn.click()

                # Aguarda transição para a tela de senha
                await page.wait_for_url("**/login/details*", timeout=10000)

                # Step 2: preenche senha
                pass_input = page.locator("input[type=password]").first
                await pass_input.wait_for(timeout=10000)
                await pass_input.fill(self.password)

                btn2 = page.locator("button[type=submit]").first
                await btn2.click()

                # Aguarda redirecionamento para área autenticada
                await page.wait_for_function(
                    "() => !window.location.pathname.startsWith('/login')",
                    timeout=15000,
                )

                final_url = page.url
                if "/login" in final_url:
                    raise Exception(f"Login falhou — ainda em /login. URL: {final_url}")

                print(f"[JusBrasilSession] Redirecionado para: {final_url}")

                # Extrai todos os cookies da sessão autenticada
                all_cookies = await context.cookies()
                return {c["name"]: c["value"] for c in all_cookies}

            finally:
                await browser.close()

    async def _flaresolverr_get(self, url: str, cookies: list[dict]) -> str:
        """Faz GET via FlareSolverr com cookies de sessão injetados."""
        payload: dict = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": 90000,
        }
        if cookies:
            payload["cookies"] = cookies

        async with httpx.AsyncClient(timeout=110) as client:
            resp = await client.post(f"{self.flaresolverr_url}/v1", json=payload)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "ok":
                raise Exception(f"FlareSolverr: {data.get('message')}")

            return data.get("solution", {}).get("response", "")

    def _cookies_as_list(self) -> list[dict]:
        """Converte cookies para o formato que o FlareSolverr aceita."""
        return [
            {"name": name, "value": value, "domain": ".jusbrasil.com.br"}
            for name, value in self._cookies.items()
        ]

    def invalidate(self):
        """Força re-autenticação na próxima chamada."""
        self._authenticated = False
        self._cookies = {}
