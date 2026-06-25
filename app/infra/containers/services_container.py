from functools import lru_cache

from app.interface.config.settings import AppSettings
from app.infra.search.searxng_client import SearxngClient
from app.infra.search.flaresolverr_client import FlareSolverrClient
from app.application.user_cases.run_search import RunSearchUseCase


@lru_cache
def get_settings():
    return AppSettings()


def get_search_client():
    settings = get_settings()

    return SearxngClient(
        settings.SEARXNG_URL
    )


def get_flaresolverr_client():
    settings = get_settings()

    return FlareSolverrClient(
        settings.FLARESOLVERR_URL
    )


def get_run_search_use_case():
    # Fábrica atualizada para injetar os dois clientes necessários no caso de uso
    return RunSearchUseCase(
        search_client=get_search_client(),
        flaresolverr_client=get_flaresolverr_client()
    )
