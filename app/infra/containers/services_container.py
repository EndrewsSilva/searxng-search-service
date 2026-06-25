from functools import lru_cache
from typing import Optional

from app.interface.config.settings import AppSettings
from app.infra.search.searxng_client import SearxngClient
from app.infra.search.flaresolverr_client import FlareSolverrClient
from app.infra.search.datajud_client import DataJudClient
from app.application.user_cases.run_search import RunSearchUseCase


@lru_cache
def get_settings():
    return AppSettings()


@lru_cache
def get_search_client():
    return SearxngClient(get_settings().SEARXNG_URL)


@lru_cache
def get_flaresolverr_client():
    return FlareSolverrClient(get_settings().FLARESOLVERR_URL)


@lru_cache
def get_datajud_client() -> DataJudClient:
    # Usa chave do .env se configurada, caso contrário usa a chave pública do CNJ
    key = get_settings().DATAJUD_API_KEY or DataJudClient.PUBLIC_KEY
    return DataJudClient(api_key=key)


@lru_cache
def get_run_search_use_case():
    return RunSearchUseCase(
        search_client=get_search_client(),
        flaresolverr_client=get_flaresolverr_client(),
        datajud_client=get_datajud_client(),
    )
