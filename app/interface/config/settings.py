from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):

    NAME: str = "SearXNG Search Service"
    DESCRIPTION: str = "Microserviço de busca baseado em SearXNG"
    VERSION: str = "1.0.0"

    HOST: str = "0.0.0.0"
    PORT: int = 5006

    SEARXNG_URL: str = "http://localhost:8080"
    FLARESOLVERR_URL: str = "http://localhost:8191"

    REQUEST_TIMEOUT: int = 60

    MAX_RESULTS: int = 10

    LOG_LEVEL: str = "INFO"

    # Chave da API pública do DataJud (CNJ).
    # Cadastro gratuito em: https://api-publica.datajud.cnj.jus.br/
    DATAJUD_API_KEY: str = ""

    class Config:
        env_file = ".env"