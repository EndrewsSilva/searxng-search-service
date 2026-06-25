"""
Extração de entidades e relacionamentos via HuggingFace Inference API.
Modelo: mistralai/Mistral-7B-Instruct-v0.2 (gratuito, rate-limited)

Retorna JSON estruturado com entidades (Person, Company, Organization, Event)
e relacionamentos entre elas.
"""
import json
import logging
import re
import httpx

logger = logging.getLogger(__name__)

HF_API_BASE = "https://api-inference.huggingface.co/models"
MODEL = "mistralai/Mistral-7B-Instruct-v0.2"

EXTRACT_PROMPT = """Você é um analista de compliance brasileiro. Analise o texto abaixo e extraia APENAS entidades e relacionamentos REAIS mencionados.

Retorne SOMENTE JSON válido, sem texto adicional:
{{
  "entities": [
    {{"type": "Person", "name": "Nome Completo", "attributes": {{"occupation": "...", "role": "..."}}}},
    {{"type": "Company", "name": "Nome Empresa", "attributes": {{"cnpj": "...", "sector": "..."}}}},
    {{"type": "Organization", "name": "Nome Org", "attributes": {{"type": "ONG|governo|partido|sindicato"}}}},
    {{"type": "Event", "name": "Descrição do evento", "attributes": {{"type": "fraude|corrupção|sanção|crime|notícia", "date": "..."}}}}
  ],
  "relationships": [
    {{"from": "Nome A", "to": "Nome B", "type": "IS_SOCIO|WORKS_AT|RELATED_TO|MEMBER_OF|MENTIONED_IN", "attributes": {{"role": "...", "since": "..."}}}}
  ]
}}

Texto:
{text}"""


class EntityExtractor:
    def __init__(self, hf_token: str):
        self.hf_token = hf_token
        self.headers = {
            "Authorization": f"Bearer {hf_token}",
            "Content-Type": "application/json",
        }

    async def extract(self, text: str, target_name: str = "") -> dict:
        if not text or not self.hf_token:
            return {"entities": [], "relationships": []}

        # Trunca o texto para evitar exceder o contexto do modelo
        truncated = text[:2000]
        prompt = EXTRACT_PROMPT.format(text=truncated)

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{HF_API_BASE}/{MODEL}/v1/chat/completions",
                    headers=self.headers,
                    json={
                        "model": MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1024,
                        "temperature": 0.05,
                        "stream": False,
                    },
                )

                if resp.status_code == 503:
                    logger.warning("[EntityExtractor] Modelo carregando, pulando extração")
                    return {"entities": [], "relationships": []}

                resp.raise_for_status()
                data = resp.json()
                raw = data["choices"][0]["message"]["content"].strip()
                return self._parse_json(raw)

        except httpx.HTTPStatusError as e:
            logger.warning(f"[EntityExtractor] HTTP {e.response.status_code}: {e.response.text[:200]}")
            return {"entities": [], "relationships": []}
        except Exception as e:
            logger.error(f"[EntityExtractor] Erro: {e}")
            return {"entities": [], "relationships": []}

    @staticmethod
    def _parse_json(raw: str) -> dict:
        # Extrai o bloco JSON da resposta do modelo
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            return {"entities": [], "relationships": []}
        try:
            data = json.loads(match.group(0))
            return {
                "entities": data.get("entities", []),
                "relationships": data.get("relationships", []),
            }
        except json.JSONDecodeError:
            return {"entities": [], "relationships": []}
