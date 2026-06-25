"""
Gera o relatório de compliance seção por seção usando HF Inference API (Mistral-7B).
Cada seção combina dados estruturados do grafo + chunks semânticos.
"""
import json
import logging
import httpx

logger = logging.getLogger(__name__)

HF_API_BASE = "https://api-inference.huggingface.co/models"
MODEL = "mistralai/Mistral-7B-Instruct-v0.2"

SYSTEM_PROMPT = (
    "Você é um analista sênior de compliance brasileiro. "
    "Redija relatórios objetivos, factuais e baseados exclusivamente nas informações fornecidas. "
    "Nunca invente dados. Quando não houver informação suficiente, diga 'Sem informações disponíveis para esta seção.' "
    "Use português formal."
)

SECTIONS = {
    "identificacao_perfil": {
        "title": "1. Identificação e Perfil",
        "semantic_query": "profissão ocupação empresa sócio familiar parentesco cônjuge",
        "prompt_template": (
            "Com base nos dados abaixo, redija a seção de **Identificação e Perfil** do alvo '{target}'.\n"
            "Inclua: profissão/ocupação, empresas vinculadas (e papel — sócio, funcionário, etc.), "
            "pessoas relacionadas (familiares, sócios, associados conhecidos).\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes das fontes:\n{chunks}"
        ),
    },
    "processos_judiciais": {
        "title": "2. Participação em Processos Judiciais",
        "semantic_query": "processo judicial requerente requerido autor réu parte terceiro CNJ tribunal",
        "prompt_template": (
            "Com base nos dados abaixo, redija a seção **Participação em Processos Judiciais** do alvo '{target}'.\n"
            "Organize por polo (ativo/passivo/terceiro). Mencione número CNJ, classe, assunto, tribunal e status.\n"
            "Destaque processos críticos (improbidade, criminal, alta complexidade).\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes:\n{chunks}"
        ),
    },
    "compliance_integridade": {
        "title": "3. Compliance e Integridade",
        "semantic_query": (
            "sanção OFAC COAF PEP político exposição corrupção lavagem dinheiro fraude improbidade "
            "irregular ilícito evasão fiscal tributário crime mídia negativa"
        ),
        "prompt_template": (
            "Com base nos dados abaixo, redija a seção **Compliance e Integridade** do alvo '{target}'.\n"
            "Verifique e relate:\n"
            "- Presença em listas de sanções (OFAC, COAF, ONU, UE)\n"
            "- Exposição como Pessoa Politicamente Exposta (PEP)\n"
            "- Mídias negativas e notícias desfavoráveis\n"
            "- Crimes financeiros: lavagem, corrupção, fraude, evasão fiscal\n"
            "- Improbidade administrativa\n"
            "- Irregularidades societárias\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes:\n{chunks}"
        ),
    },
    "entidades_organizacoes": {
        "title": "4. Entidades e Organizações",
        "semantic_query": "ONG entidade sem fins lucrativos associação fundação organização partido sindicato",
        "prompt_template": (
            "Com base nos dados abaixo, redija a seção **Entidades e Organizações** do alvo '{target}'.\n"
            "Identifique participação em: ONGs, associações, fundações, partidos políticos, sindicatos, "
            "entidades de classe e organizações sem fins lucrativos.\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes:\n{chunks}"
        ),
    },
    "ramos_sensiveis": {
        "title": "5. Atuação em Ramos Sensíveis (Lavagem de Dinheiro)",
        "semantic_query": (
            "ouro joias veículo imóvel câmbio factoring imobiliária armas importação exportação "
            "cannabis agrotóxico mineração licitação contrato público"
        ),
        "prompt_template": (
            "Com base nos dados abaixo, redija a seção **Atuação em Ramos Sensíveis** do alvo '{target}'.\n"
            "Verifique participação nos setores de risco para lavagem de dinheiro:\n"
            "joias/metais preciosos, veículos, imóveis, câmbio, factoring, armas, importação/exportação, "
            "cannabis, agronegócio, licitações públicas, serviços financeiros não bancários.\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes:\n{chunks}"
        ),
    },
    "atividades_risco": {
        "title": "6. Atividades de Risco",
        "semantic_query": (
            "criptoativo bitcoin ethereum blockchain ativo virtual bet aposta loteria "
            "jogo azar cassino shell company offshore paraíso fiscal"
        ),
        "prompt_template": (
            "Com base nos dados abaixo, redija a seção **Atividades de Risco** do alvo '{target}'.\n"
            "Verifique envolvimento com:\n"
            "- Ativos virtuais / criptoativos (Bitcoin, exchanges, DeFi)\n"
            "- Empresas de apostas esportivas (BETs) e jogos de azar\n"
            "- Shell companies e estruturas offshore\n"
            "- Bancos fantasma (shell banks)\n"
            "- Paraísos fiscais\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes:\n{chunks}"
        ),
    },
}

RISK_KEYWORDS = {
    "CRITICAL": ["lavagem", "corrupção", "fraude", "sanção", "preso", "condenado", "crime"],
    "HIGH": ["investigado", "suspeito", "indiciado", "improbidade", "réu", "ação penal"],
    "MEDIUM": ["processo", "autuado", "notícia negativa", "irregular"],
    "LOW": [],
}


def _infer_risk(text: str) -> str:
    text_lower = text.lower()
    for level, keywords in RISK_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return level
    return "N/A"


class ReportGenerator:
    def __init__(self, hf_token: str):
        self.hf_token = hf_token
        self.headers = {
            "Authorization": f"Bearer {hf_token}",
            "Content-Type": "application/json",
        }

    async def generate_section(
        self,
        section_key: str,
        target: str,
        graph_data: list[dict],
        chunks: list[dict],
    ) -> tuple[str, str]:
        """Retorna (content, risk_level)."""
        section = SECTIONS[section_key]
        prompt = self._build_prompt(section, target, graph_data, chunks)

        if not self.hf_token:
            content = self._fallback_content(section_key, graph_data, chunks)
            return content, _infer_risk(content)

        try:
            content = await self._call_hf(prompt)
            if not content:
                content = self._fallback_content(section_key, graph_data, chunks)
        except Exception as e:
            logger.error(f"[ReportGenerator] Seção '{section_key}' falhou: {e}")
            content = self._fallback_content(section_key, graph_data, chunks)

        risk = _infer_risk(content)
        return content, risk

    async def _call_hf(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{HF_API_BASE}/{MODEL}/v1/chat/completions",
                headers=self.headers,
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 600,
                    "temperature": 0.2,
                    "stream": False,
                },
            )

            if resp.status_code == 503:
                logger.warning("[ReportGenerator] Modelo carregando (503)")
                return ""

            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()

    @staticmethod
    def _build_prompt(section: dict, target: str, graph_data: list[dict], chunks: list[dict]) -> str:
        graph_text = (
            json.dumps(graph_data, ensure_ascii=False, indent=2)[:1500]
            if graph_data
            else "Nenhum dado estruturado encontrado."
        )
        chunks_text = (
            "\n---\n".join(c.get("text", "") for c in chunks[:4])[:1500]
            if chunks
            else "Nenhum trecho relevante encontrado."
        )
        return section["prompt_template"].format(
            target=target,
            graph_data=graph_text,
            chunks=chunks_text,
        )

    @staticmethod
    def _fallback_content(section_key: str, graph_data: list[dict], chunks: list[dict]) -> str:
        """Gera conteúdo mínimo sem LLM quando a API não está disponível."""
        parts = []

        if graph_data:
            parts.append("**Dados estruturados do grafo:**")
            for row in graph_data[:10]:
                row_clean = {k: v for k, v in row.items() if v}
                if row_clean:
                    parts.append("- " + "; ".join(f"{k}: {v}" for k, v in row_clean.items()))

        if chunks:
            parts.append("\n**Trechos relevantes das fontes:**")
            for chunk in chunks[:3]:
                parts.append(f"- {chunk.get('text', '')[:300]}...")

        if not parts:
            return "Sem informações disponíveis para esta seção."

        return "\n".join(parts)
