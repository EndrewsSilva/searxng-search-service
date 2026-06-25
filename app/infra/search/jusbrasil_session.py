import asyncio
import logging

import httpx


logger = logging.getLogger(__name__)


class JusBrasilSession:
    """
    Gerencia autenticação e sessão autenticada no JusBrasil.

    Fluxo:
    1. FlareSolverr faz GET /login → obtém cf_clearance + user-agent (fingerprint-bound)
    2. Playwright injeta cf_clearance e navega a /login no MESMO contexto
    3. Playwright faz login e permanece ABERTO (mesmo browser/contexto)
    4. get_page() usa o contexto persistente — mesmo fingerprint = cf_clearance válido

    Credenciais no .env:
        JUSBRASIL_EMAIL=seu@email.com
        JUSBRASIL_PASSWORD=suasenha
    """

    LOGIN_URL = "https://www.jusbrasil.com.br/login"

    def __init__(self, flaresolverr_url: str, email: str, password: str):
        self.flaresolverr_url = flaresolverr_url.rstrip("/")
        self.email = email
        self.password = password

        self._lock = asyncio.Lock()
        self._authenticated = False

        # Playwright resources kept alive after login
        self._playwright = None
        self._browser = None
        self._context = None

    async def authenticate(self) -> bool:
        async with self._lock:
            if self._authenticated:
                return True

            # Close stale browser before re-authenticating
            await self._close_playwright()

            try:
                print("[JusBrasilSession] Iniciando autenticação via Playwright...")

                cf_cookies, ua = await self._get_cloudflare_clearance()
                print(f"[JusBrasilSession] cf_clearance obtido ({len(cf_cookies)} cookies)")

                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
                self._context = await self._browser.new_context(
                    user_agent=ua,
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                )

                # Inject cf_clearance so Cloudflare doesn't re-challenge
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
                await self._context.add_cookies(pw_cookies)

                page = await self._context.new_page()
                try:
                    await page.goto(self.LOGIN_URL, timeout=30000, wait_until="networkidle")

                    title = await page.title()
                    if "momento" in title.lower() or "checking" in title.lower():
                        raise Exception(f"Cloudflare challenge não resolvido. Título: {title}")

                    email_input = page.locator("input[type=email], input[name=email]").first
                    await email_input.wait_for(timeout=10000)
                    await email_input.fill(self.email)

                    btn = page.locator("button[type=submit]").first
                    await btn.click()

                    await page.wait_for_url("**/login/details*", timeout=10000)

                    pass_input = page.locator("input[type=password]").first
                    await pass_input.wait_for(timeout=10000)
                    await pass_input.fill(self.password)

                    btn2 = page.locator("button[type=submit]").first
                    await btn2.click()

                    await page.wait_for_function(
                        "() => !window.location.pathname.startsWith('/login')",
                        timeout=15000,
                    )

                    final_url = page.url
                    if "/login" in final_url:
                        raise Exception(f"Login falhou — ainda em /login. URL: {final_url}")

                    print(f"[JusBrasilSession] Redirecionado para: {final_url}")

                    all_cookies = await self._context.cookies()
                    cookie_names = [c["name"] for c in all_cookies]
                    print(f"[JusBrasilSession] Login concluído. Cookies: {cookie_names}")

                    self._authenticated = True
                    logger.info("[JusBrasilSession] Autenticado com sucesso no JusBrasil")
                    return True

                except Exception:
                    await self._close_playwright()
                    raise

                finally:
                    await page.close()

            except Exception as e:
                logger.error(f"[JusBrasilSession] Falha na autenticação: {str(e)}")
                print(f"[JusBrasilSession] ERRO: {str(e)}")
                return False

    async def get_page(self, url: str) -> str:
        """Busca uma página do JusBrasil usando o browser Playwright autenticado."""
        if not self._authenticated:
            success = await self.authenticate()
            if not success:
                logger.warning(f"[JusBrasilSession] Sem autenticação: {url}")
                return ""

        if not self._context:
            logger.error("[JusBrasilSession] Contexto Playwright não disponível")
            return ""

        page = await self._context.new_page()
        try:
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception:
                # On networkidle timeout, still capture whatever rendered
                pass

            html = await page.content()
            page_title = await page.title()
            print(
                f"[JusBrasilSession] GET {url[:80]}\n"
                f"  title={page_title[:80]} | html_len={len(html)}"
            )

            return html

        except Exception as e:
            logger.error(f"[JusBrasilSession] Fetch error {url}: {e}")
            return ""
        finally:
            await page.close()

    async def _get_cloudflare_clearance(self) -> tuple[dict[str, str], str]:
        """Usa FlareSolverr para obter cf_clearance (bypass Cloudflare) e user-agent."""
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

    async def _close_playwright(self):
        for resource, attr in [
            (self._context, "_context"),
            (self._browser, "_browser"),
            (self._playwright, "_playwright"),
        ]:
            if resource:
                try:
                    await resource.close() if attr != "_playwright" else await resource.stop()
                except Exception:
                    pass
            setattr(self, attr, None)

    def invalidate(self):
        """Força re-autenticação na próxima chamada (mantém browser aberto)."""
        self._authenticated = False

    async def close(self):
        """Fecha o browser Playwright e invalida a sessão."""
        await self._close_playwright()
        self._authenticated = False
