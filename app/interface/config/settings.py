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

    # Credenciais da conta gratuita no JusBrasil (para acesso autenticado).
    # Conta gratuita em: https://www.jusbrasil.com.br/cadastro
    JUSBRASIL_EMAIL: str = ""
    JUSBRASIL_PASSWORD: str = ""

    # Graph RAG — Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password123"

    # HuggingFace Inference API (token gratuito em https://huggingface.co/settings/tokens)
    HF_TOKEN: str = ""

    class Config:
        env_file = ".env"