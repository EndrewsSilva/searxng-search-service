from functools import lru_cache
from typing import Optional

from app.interface.config.settings import AppSettings
from app.infra.search.searxng_client import SearxngClient
from app.infra.search.flaresolverr_client import FlareSolverrClient
from app.infra.search.datajud_client import DataJudClient
from app.infra.search.jusbrasil_session import JusBrasilSession
from app.infra.search.agent_orchestrator import AgentOrchestrator
from app.application.user_cases.run_search import RunSearchUseCase
from app.infra.graph.neo4j_client import Neo4jClient
from app.application.user_cases.run_compliance_report import RunComplianceReportUseCase


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
    key = get_settings().DATAJUD_API_KEY or DataJudClient.PUBLIC_KEY
    return DataJudClient(api_key=key)


@lru_cache
def get_jusbrasil_session() -> Optional[JusBrasilSession]:
    settings = get_settings()
    if not settings.JUSBRASIL_EMAIL or not settings.JUSBRASIL_PASSWORD:
        return None
    return JusBrasilSession(
        flaresolverr_url=settings.FLARESOLVERR_URL,
        email=settings.JUSBRASIL_EMAIL,
        password=settings.JUSBRASIL_PASSWORD,
    )


@lru_cache
def get_run_search_use_case():
    """Used by the /search endpoint (kept for backwards compatibility)."""
    return RunSearchUseCase(
        search_client=get_search_client(),
        flaresolverr_client=get_flaresolverr_client(),
        datajud_client=get_datajud_client(),
        jusbrasil_session=get_jusbrasil_session(),
    )


@lru_cache
def get_neo4j_client() -> Neo4jClient:
    s = get_settings()
    return Neo4jClient(
        uri=s.NEO4J_URI,
        username=s.NEO4J_USER,
        password=s.NEO4J_PASSWORD,
    )


@lru_cache
def get_agent_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator(
        flaresolverr_client=get_flaresolverr_client(),
        search_client=get_search_client(),
        jusbrasil_session=get_jusbrasil_session(),
        datajud_client=get_datajud_client(),
    )


@lru_cache
def get_compliance_use_case() -> RunComplianceReportUseCase:
    return RunComplianceReportUseCase(
        agent_orchestrator=get_agent_orchestrator(),
        neo4j=get_neo4j_client(),
        hf_token=get_settings().HF_TOKEN,
    )
