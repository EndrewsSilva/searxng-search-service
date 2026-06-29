"""
ReportGenerator: gera o relatório de investigação seção por seção.

Quando HF_TOKEN está configurado: usa Mistral-7B-Instruct via HF Inference API.
Caso contrário: usa fallbacks estruturados que produzem o formato exato do template:

  Sumário Executivo
  Identificação e Contexto (campos Nome, CPF, Endereço, Formação…)
  Participações Societárias (blocos por empresa com CNPJ, QSA…)
  Atividades Empresariais e Vínculos Profissionais
  Produção Acadêmica e Reconhecimentos
  Processos e Alertas de Risco

Todas as seções usam citações [N] que mapeiam para as referências numeradas.
"""
import json
import logging
import re
import httpx

logger = logging.getLogger(__name__)

HF_API_BASE = "https://api-inference.huggingface.co/models"
MODEL       = "mistralai/Mistral-7B-Instruct-v0.2"

SYSTEM_PROMPT = (
    "Você é um analista sênior de compliance e investigação corporativa brasileiro. "
    "Redija relatórios objetivos, factuais e baseados EXCLUSIVAMENTE nas informações fornecidas. "
    "NUNCA invente dados, CNPJs, endereços ou cargos. "
    "Use o formato de relatório profissional exibido abaixo como modelo. "
    "Cite fontes com referências numéricas [1], [2], etc. "
    "Use português formal."
)

SECTIONS = {
    "sumario_executivo": {
        "title": "Sumário Executivo",
        "semantic_query": "empresa CNPJ sócio participação vínculo representante homônimo ativo inativo",
        "prompt_template": (
            "Com base nos dados abaixo sobre '{target}', redija o **Sumário Executivo** do relatório.\n"
            "Use bullets completos com frases descritivas. Ex.:\n"
            "- Foi identificada 1 empresa com participação societária de {target}: Nome Empresa (CNPJ XX), na qual figura como Sócio [1].\n"
            "- O total de empresas ativas é 0 (zero), conforme ConsultaSocio [1].\n"
            "- {target} mantém vínculo profissional como CARGO na EMPRESA [2][3].\n"
            "- O número de homônimos para o nome \"{target}\" é N, conforme ConsultaSocio [1].\n\n"
            "Os números [N] devem corresponder às URLs fornecidas abaixo:\n{url_refs}\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes das fontes:\n{chunks}"
        ),
    },
    "identificacao_contexto": {
        "title": "Identificação e Contexto",
        "semantic_query": "nome CPF nascimento data endereço telefone cidade estado formação graduação mestrado especialização universidade",
        "prompt_template": (
            "Com base nos dados abaixo sobre '{target}', redija a seção **Identificação e Contexto**.\n"
            "Use exatamente este formato:\n"
            "Nome completo: {target} [N]\n"
            "CPF: (valor ou 'não disponível nas fontes consultadas')\n"
            "Data de nascimento: (valor ou 'não disponível nas fontes consultadas')\n"
            "Endereço profissional: (valor) [N]\n"
            "Telefone: (valor) [N]\n"
            "Formação: Graduação em X pela Y (AAAA); Especialização em X pela Y (AAAA); … [N]\n\n"
            "Os números [N] mapeiam para:\n{url_refs}\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes das fontes:\n{chunks}"
        ),
    },
    "participacoes_societarias": {
        "title": "Participações Societárias",
        "semantic_query": "CNPJ empresa sócio QSA quadro societário participação situação ativa inativa capital social data abertura natureza jurídica",
        "prompt_template": (
            "Com base nos dados abaixo sobre '{target}', redija a seção **Participações Societárias**.\n"
            "Para cada empresa, use exatamente este bloco:\n\n"
            "Nome da Empresa\n"
            "  CNPJ: XX.XXX.XXX/XXXX-XX [N]\n"
            "  Qualificação: (Sócio / Sócio-Administrador / etc.) [N]\n"
            "  Situação cadastral: (Ativa / Inativa) [N]\n"
            "  Capital social: R$ X.XXX,XX [N]\n"
            "  Data de abertura: DD/MM/AAAA [N]\n"
            "  Atividade principal: código - descrição [N]\n"
            "  Endereço: (endereço completo) [N]\n"
            "  QSA da empresa:\n"
            "    Nome Sócio 1 - Qualificação [N]\n"
            "    Nome Sócio 2 - Qualificação [N]\n\n"
            "Se {target} NÃO consta no QSA mas tem vínculo, indique: "
            "'Nota: {target} não consta no Quadro Societário desta empresa [N]'\n\n"
            "Os [N] mapeiam para:\n{url_refs}\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes das fontes:\n{chunks}"
        ),
    },
    "vinculos_profissionais": {
        "title": "Atividades Empresariais e Vínculos Profissionais",
        "semantic_query": "emprego cargo diretor professor gerente representante celetista CLT histórico profissional função desde período empresa banco universidade",
        "prompt_template": (
            "Com base nos dados abaixo sobre '{target}', redija a seção "
            "**Atividades Empresariais e Vínculos Profissionais**.\n\n"
            "**Vínculos Atuais:**\n"
            "Para cada vínculo atual: empresa, cargo, regime (celetista/estatutário), carga horária, desde quando. [N]\n\n"
            "**Histórico Profissional (ordem cronológica inversa):**\n"
            "  Empresa - Cargo (AAAA-AAAA) [N]\n\n"
            "Os [N] mapeiam para:\n{url_refs}\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes das fontes:\n{chunks}"
        ),
    },
    "producao_academica": {
        "title": "Produção Acadêmica e Reconhecimentos",
        "semantic_query": "publicação artigo livro dissertação mestrado doutorado prêmio comenda reconhecimento premiação conferência palestra",
        "prompt_template": (
            "Com base nos dados abaixo sobre '{target}', redija a seção "
            "**Produção Acadêmica e Reconhecimentos**.\n"
            "Use estes subformatos:\n"
            "Publicação: 'título', Veículo, data [N]\n"
            "Prêmio: Nome do Prêmio, Instituição Concedente, Ano [N]\n"
            "Dissertação de mestrado: 'título', Universidade, Ano [N]\n"
            "Se não houver dados: 'Sem informações disponíveis para esta seção.'\n\n"
            "Os [N] mapeiam para:\n{url_refs}\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes das fontes:\n{chunks}"
        ),
    },
    "processos_alertas": {
        "title": "Processos e Alertas de Risco",
        "semantic_query": (
            "processo judicial CNJ tribunal sentença condenação fraude corrupção irregularidade sanção PEP "
            "improbidade lavagem réu autor polo recurso acórdão decisão"
        ),
        "prompt_template": (
            "Com base nos dados abaixo sobre '{target}', redija a seção "
            "**Processos e Alertas de Risco**.\n\n"
            "**Processos Judiciais:**\n"
            "Para cada processo: número CNJ, tribunal, classe, polo (autor/réu), assunto, status. [N]\n\n"
            "**Alertas de Compliance:**\n"
            "- Listas de sanções (OFAC, COAF, ONU, UE)\n"
            "- Exposição como PEP\n"
            "- Mídias negativas ou notícias desfavoráveis\n\n"
            "Conclua com nível de risco: CRITICAL / HIGH / MEDIUM / LOW / N/A.\n\n"
            "Os [N] mapeiam para:\n{url_refs}\n\n"
            "Dados do grafo:\n{graph_data}\n\n"
            "Trechos relevantes das fontes:\n{chunks}"
        ),
    },
}

RISK_KEYWORDS = {
    "CRITICAL": ["lavagem", "corrupção", "fraude", "sanção", "preso", "condenado", "crime", "improbidade"],
    "HIGH":     ["investigado", "suspeito", "indiciado", "réu", "ação penal", "irregularidade"],
    "MEDIUM":   ["processo", "autuado", "notícia negativa", "irregular", "recurso"],
}


def _infer_risk(text: str) -> str:
    lower = text.lower()
    for level, keywords in RISK_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return level
    return "N/A"


class ReportGenerator:
    def __init__(self, hf_token: str):
        self.hf_token = hf_token
        self.headers  = {
            "Authorization": f"Bearer {hf_token}",
            "Content-Type":  "application/json",
        }

    async def generate_section(
        self,
        section_key: str,
        target: str,
        graph_data: list[dict],
        chunks: list[dict],
        url_to_ref: dict[str, int] | None = None,
    ) -> tuple[str, str]:
        """Returns (content, risk_level)."""
        url_to_ref = url_to_ref or {}
        section    = SECTIONS[section_key]

        if not self.hf_token:
            content = self._fallback_content(section_key, graph_data, chunks, target, url_to_ref)
            return content, _infer_risk(content)

        prompt = self._build_prompt(section, target, graph_data, chunks, url_to_ref)
        try:
            content = await self._call_hf(prompt)
            if not content:
                content = self._fallback_content(section_key, graph_data, chunks, target, url_to_ref)
        except Exception as e:
            logger.error(f"[ReportGenerator] '{section_key}' HF call failed: {e}")
            content = self._fallback_content(section_key, graph_data, chunks, target, url_to_ref)

        return content, _infer_risk(content)

    async def _call_hf(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{HF_API_BASE}/{MODEL}/v1/chat/completions",
                headers=self.headers,
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                    "max_tokens": 900,
                    "temperature": 0.15,
                    "stream": False,
                },
            )
            if resp.status_code == 503:
                logger.warning("[ReportGenerator] Model loading (503)")
                return ""
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()

    @staticmethod
    def _build_prompt(
        section: dict,
        target: str,
        graph_data: list[dict],
        chunks: list[dict],
        url_to_ref: dict[str, int],
    ) -> str:
        graph_text = (
            json.dumps(graph_data, ensure_ascii=False, indent=2)[:2000]
            if graph_data else "Nenhum dado estruturado encontrado."
        )

        chunks_parts: list[str] = []
        for i, c in enumerate(chunks[:6], 1):
            url   = c.get("source_url", "")
            num   = url_to_ref.get(url, i)
            text  = c.get("text", "")[:400]
            chunks_parts.append(f"[{num}] {url}\n{text}")
        chunks_text = "\n---\n".join(chunks_parts) or "Nenhum trecho relevante encontrado."

        url_refs_lines = [f"[{num}] {url}" for url, num in sorted(url_to_ref.items(), key=lambda x: x[1])]
        url_refs = "\n".join(url_refs_lines[:12]) or "Sem referências indexadas."

        return section["prompt_template"].format(
            target=target,
            graph_data=graph_text,
            chunks=chunks_text,
            url_refs=url_refs,
        )

    @staticmethod
    def _fallback_content(
        section_key: str,
        graph_data: list[dict],
        chunks: list[dict],
        target: str,
        url_to_ref: dict[str, int],
    ) -> str:
        dispatch = {
            "sumario_executivo":          ReportGenerator._fallback_sumario,
            "identificacao_contexto":     ReportGenerator._fallback_identificacao,
            "participacoes_societarias":  ReportGenerator._fallback_participacoes,
            "vinculos_profissionais":     ReportGenerator._fallback_vinculos,
            "producao_academica":         ReportGenerator._fallback_producao,
            "processos_alertas":          ReportGenerator._fallback_processos,
        }
        fn = dispatch.get(section_key)
        if fn:
            return fn(graph_data, chunks, target, url_to_ref)

        parts: list[str] = []
        if graph_data:
            parts.append("**Dados estruturados:**")
            for row in graph_data[:8]:
                clean = {k: v for k, v in row.items() if v}
                if clean:
                    parts.append("- " + "; ".join(f"{k}: {v}" for k, v in clean.items()))
        if chunks:
            parts.append("\n**Trechos relevantes:**")
            for c in chunks[:3]:
                parts.append(f"- {c.get('text', '')[:300]}…")
        return "\n".join(parts) or "Sem informações disponíveis para esta seção."

    # ─────────────────────────────────────────────────────────────────────
    # Fallbacks estruturados — produzem o formato exato do template
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _fallback_sumario(
        graph_data: list[dict],
        chunks: list[dict],
        target: str,
        url_to_ref: dict[str, int],
    ) -> str:
        full_text = " ".join(c.get("text", "") for c in chunks)

        companies  = [r for r in graph_data if r.get("empresa") or r.get("cnpj")]
        processes  = [r for r in graph_data if r.get("cnj") or r.get("cnj_number")]
        ativas     = sum(1 for c in companies if "ativa" in str(c.get("situacao", "")).lower())
        homonyms   = _count_homonyms(full_text)

        def cit(c: dict) -> str:
            return _cite(c.get("source_url", ""), url_to_ref)

        lines: list[str] = []

        if companies:
            co = companies[0]
            co_name = co.get("empresa") or co.get("name") or "empresa identificada"
            cnpj    = co.get("cnpj", "")
            role    = _normalize_role(co.get("papel", ""))
            src     = cit(co) or _chunk_cite(chunks, "consultasocio.com", url_to_ref)
            cnpj_part = f" (CNPJ {cnpj})" if cnpj else ""
            lines.append(
                f"- Foi identificad{'a' if len(companies)==1 else 'as'} "
                f"**{len(companies)} empresa{'s' if len(companies)>1 else ''}** "
                f"com participação societária de {target}: "
                f"{co_name}{cnpj_part}, na qual figura como {role} {src}."
            )
            if len(companies) > 1:
                for co2 in companies[1:]:
                    n2  = co2.get("empresa") or co2.get("name") or ""
                    c2  = co2.get("cnpj", "")
                    r2  = _normalize_role(co2.get("papel", ""))
                    s2  = cit(co2) or src
                    lines.append(f"- Também identificada participação em **{n2}** (CNPJ {c2}), como {r2} {s2}.")

        src_cs = _chunk_cite(chunks, "consultasocio.com", url_to_ref)
        lines.append(
            f"- O total de empresas ativas com participação do sócio é "
            f"**{ativas}** (zero), conforme registro do ConsultaSocio {src_cs}."
            if ativas == 0 else
            f"- Total de empresas ativas: **{ativas}** {src_cs}."
        )

        vinculos = _extract_vinculos_atuais(full_text, target)
        if vinculos:
            src_esc = _chunk_cite(chunks, "escavador.com", url_to_ref)
            lines.append(
                f"- {target} mantém vínculo profissional como **{vinculos[0]}** {src_esc}."
            )

        if homonyms > 0:
            lines.append(
                f"- O número de homônimos para o nome \"{target}\" é **{homonyms}**, "
                f"conforme ConsultaSocio {src_cs}."
            )

        if processes:
            lines.append(
                f"- **{len(processes)} processo{'s' if len(processes)>1 else ''} judicial{'is' if len(processes)>1 else ''}** "
                f"identificado(s) nas fontes."
            )

        location = _extract_primary_location(full_text)
        if location:
            lines.append(f"- Localização principal identificada: **{location}**.")

        return "\n".join(lines) if lines else "Sem dados suficientes para o sumário executivo."

    @staticmethod
    def _fallback_identificacao(
        graph_data: list[dict],
        chunks: list[dict],
        target: str,
        url_to_ref: dict[str, int],
    ) -> str:
        full_text = " ".join(c.get("text", "") for c in chunks)

        src_esc = _chunk_cite(chunks, "escavador.com", url_to_ref)
        src_cs  = _chunk_cite(chunks, "consultasocio.com", url_to_ref)
        src_any = src_esc or src_cs or ""

        nome       = _extract_field(full_text, [r"Nome(?:\s+completo)?:\s*([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+){1,6})"])
        cpf        = _extract_field(full_text, [r"\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b"])
        nascimento = _extract_field(full_text, [
            r"(?:nascimento|nasceu|nasc\.?)[:\s]+(\d{2}/\d{2}/\d{4})",
            r"nascid[oa]\s+em\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
        ])
        endereco   = _extract_field(full_text, [
            r"(?:Endereço|End\.?)[:\s]+([^\n]{10,100})",
            r"(Rua\s+\w[\w\s,º°]+\d{5}-?\d{3})",
            r"(Av(?:enida|\.)\s+\w[\w\s,º°]+\d{5}-?\d{3})",
        ])
        telefone   = _extract_field(full_text, [
            r"(\(0?\d{2,3}\)\s*\d{4,5}[-\s]?\d{4})",
            r"(\b0\d{2}\s*\d{4,5}[-\s]?\d{4}\b)",
        ])
        formacao   = _extract_formacao(full_text)

        def field(label: str, value: str, cite: str = src_any) -> str:
            v = value or "não disponível nas fontes consultadas"
            c = f" {cite}" if cite and value else ""
            return f"**{label}:** {v}{c}"

        lines = [
            field("Nome completo", nome or target, src_any),
            field("CPF", cpf, src_any),
            field("Data de nascimento", nascimento, src_any),
            field("Endereço profissional", endereco, src_esc),
            field("Telefone", telefone, src_esc),
        ]

        if formacao:
            lines.append("\n**Formação:**")
            for f_item in formacao:
                lines.append(f"  - {f_item} {src_esc}")
        else:
            lines.append(field("Formação", "", src_any))

        return "\n".join(lines)

    @staticmethod
    def _fallback_participacoes(
        graph_data: list[dict],
        chunks: list[dict],
        target: str,
        url_to_ref: dict[str, int],
    ) -> str:
        full_text = " ".join(c.get("text", "") for c in chunks)
        companies_graph = [r for r in graph_data if r.get("empresa") or r.get("name")]

        # Also check CNPJ.biz chunks for QSA data
        cnpjbiz_chunks  = [c for c in chunks if "cnpj.biz" in c.get("source_url", "")]
        escavador_chunks = [c for c in chunks if "escavador.com" in c.get("source_url", "")]
        cs_chunks       = [c for c in chunks if "consultasocio.com" in c.get("source_url", "")]

        if not companies_graph and not _CNPJ_RE.search(full_text):
            return "Sem participações societárias identificadas nas fontes consultadas."

        parts: list[str] = []
        seen_cos: set[str] = set()

        def _co_cite(co_dict: dict) -> str:
            return _cite(co_dict.get("fonte") or co_dict.get("source_url", ""), url_to_ref)

        for co in companies_graph:
            name  = co.get("empresa") or co.get("name") or ""
            cnpj  = co.get("cnpj") or ""
            papel = _normalize_role(co.get("papel", ""))
            setor = co.get("setor") or co.get("sector") or ""

            if not name or name in seen_cos:
                continue
            seen_cos.add(name)

            # Look for CNPJ.biz page for this CNPJ
            cnpj_chunk = next(
                (c for c in cnpjbiz_chunks if cnpj and cnpj.replace(".", "").replace("/", "").replace("-", "") in c.get("source_url", "")),
                None,
            )
            src_cnpj = _cite(cnpj_chunk["source_url"], url_to_ref) if cnpj_chunk else ""
            src_co   = _co_cite(co) or _chunk_cite(cs_chunks, "consultasocio.com", url_to_ref)

            ctx_text = (cnpj_chunk or {}).get("text", "") or ""
            situacao = "Ativa" if "ativa" in ctx_text.lower() else ("Inativa" if "inativa" in ctx_text.lower() else "—")
            cap_social = _extract_field(ctx_text, [r"Capital[:\s]+R\$\s*([\d.,]+)"])
            data_abertura = _extract_field(ctx_text, [r"(?:Abertura|Data de abertura)[:\s]+(\d{2}/\d{2}/\d{4})"])
            ativ_principal = _extract_field(ctx_text, [r"(\d{4,5}-\d-\d{2}\s*-\s*[^\n]{5,80})"])
            endereco = _extract_field(ctx_text, [r"(?:Endereço|End\.?)[:\s]+([^\n]{15,120})"])
            natureza = _extract_field(ctx_text, [r"Natureza[^\n:]{0,30}:\s*([^\n]{5,60})"])

            # QSA from CNPJ.biz chunk
            qsa_matches = re.findall(
                r"([A-ZÀ-Ú][a-zA-Zà-ú\s]{3,60})\s*[-–]\s*(Sócio[^\n,]{0,40}|Administrador[^\n,]{0,40})",
                ctx_text,
            )

            parts.append(f"\n**{name}**")
            parts.append(f"  CNPJ: {cnpj} {src_co}")
            parts.append(f"  Qualificação: {papel} {src_co}")
            if situacao != "—":
                parts.append(f"  Situação cadastral: {situacao} {src_cnpj}")
            if cap_social:
                parts.append(f"  Capital social: R$ {cap_social} {src_cnpj}")
            if data_abertura:
                parts.append(f"  Data de abertura: {data_abertura} {src_cnpj}")
            if ativ_principal:
                parts.append(f"  Atividade principal: {ativ_principal} {src_cnpj}")
            if natureza:
                parts.append(f"  Natureza jurídica: {natureza} {src_cnpj}")
            if endereco:
                parts.append(f"  Endereço: {endereco} {src_cnpj}")

            # Check if target is NOT in QSA (only appears as employee)
            target_in_qsa = _name_in_qsa(target, qsa_matches)
            if cnpj_chunk and not target_in_qsa and papel.lower() not in ("sócio", "sócio-administrador"):
                parts.append(f"  *Nota: {target} não consta no Quadro Societário desta empresa* {src_cnpj}")

            if qsa_matches:
                parts.append("  **QSA da empresa:**")
                for q_name, q_role in qsa_matches[:5]:
                    parts.append(f"    {q_name.strip()} - {q_role.strip()} {src_cnpj}")

        if not parts:
            cnpjs_found = list(dict.fromkeys(_CNPJ_RE.findall(full_text)))[:5]
            if cnpjs_found:
                parts.append(f"CNPJs identificados nas fontes: {', '.join(cnpjs_found)}")
            else:
                return "Sem participações societárias identificadas nas fontes consultadas."

        homonyms = _count_homonyms(full_text)
        if homonyms > 0:
            src_cs = _chunk_cite(chunks, "consultasocio.com", url_to_ref)
            parts.append(
                f"\n**Observação:** {homonyms} homônimo(s) identificado(s) nas fontes {src_cs}."
            )

        return "\n".join(parts)

    @staticmethod
    def _fallback_vinculos(
        graph_data: list[dict],
        chunks: list[dict],
        target: str,
        url_to_ref: dict[str, int],
    ) -> str:
        PROFILE_URLS  = ["escavador.com/sobre", "consultasocio.com/q/sa"]
        SEARCH_URLS   = ["busca?q=", "/busca?", "/nome/", "jusbrasil.com.br/busca"]

        profile_chunks    = [c for c in chunks if any(p in c.get("source_url","") for p in PROFILE_URLS)]
        non_search_chunks = [c for c in chunks if not any(s in c.get("source_url","") for s in SEARCH_URLS)]
        priority_chunks   = profile_chunks or non_search_chunks or chunks
        target_chunks     = _filter_chunks_by_target(priority_chunks, target)
        full_text         = " ".join(c.get("text", "") for c in (target_chunks or priority_chunks))

        src_esc = _chunk_cite(priority_chunks, "escavador.com", url_to_ref)

        atuais   = _extract_vinculos_atuais(full_text, target)
        historico = _extract_historico_profissional(full_text)

        parts: list[str] = ["**Vínculos Profissionais Atuais:**"]
        if atuais:
            for v in atuais:
                parts.append(f"  - {v} {src_esc}")
        else:
            parts.append("  Sem vínculos atuais identificados nas fontes.")

        parts.append("\n**Histórico Profissional (ordem cronológica inversa):**")
        if historico:
            for h in historico:
                parts.append(f"  - {h} {src_esc}")
        else:
            parts.append("  Sem histórico profissional identificado nas fontes.")

        return "\n".join(parts)

    @staticmethod
    def _fallback_producao(
        graph_data: list[dict],
        chunks: list[dict],
        target: str,
        url_to_ref: dict[str, int],
    ) -> str:
        full_text    = " ".join(c.get("text", "") for c in chunks)
        src_esc      = _chunk_cite(chunks, "escavador.com", url_to_ref)

        publicacoes  = _extract_publicacoes(full_text)
        premios      = _extract_premios(full_text)
        dissertacoes = _extract_dissertacoes(full_text)

        if not publicacoes and not premios and not dissertacoes:
            return "Sem informações de produção acadêmica ou reconhecimentos disponíveis nas fontes consultadas."

        parts: list[str] = []
        for p in publicacoes:
            parts.append(f"Publicação: {p} {src_esc}")
        for d in dissertacoes:
            parts.append(f"Dissertação de mestrado: {d} {src_esc}")
        for p in premios:
            parts.append(f"Prêmio: {p} {src_esc}")

        return "\n".join(parts)

    @staticmethod
    def _fallback_processos(
        graph_data: list[dict],
        chunks: list[dict],
        target: str,
        url_to_ref: dict[str, int],
    ) -> str:
        processes = [r for r in graph_data if r.get("cnj") or r.get("cnj_number")]

        parts: list[str] = []
        if processes:
            parts.append(f"**Processos Judiciais ({len(processes)} identificado{'s' if len(processes)>1 else ''}):**\n")
            for i, row in enumerate(processes, 1):
                cnj      = row.get("cnj") or row.get("cnj_number") or "—"
                tribunal = row.get("tribunal") or row.get("court") or "—"
                classe   = row.get("classe") or row.get("class_") or "—"
                assunto  = row.get("assunto") or row.get("subject") or "—"
                status   = row.get("status") or "—"
                polo     = row.get("polo") or row.get("role") or "—"
                data     = row.get("data_inicio") or row.get("origin_date") or "—"
                src_url  = row.get("fonte") or row.get("source_url") or ""
                cite     = _cite(src_url, url_to_ref)

                parts.append(f"**{i}. {cnj}** {cite}")
                parts.append(f"   Tribunal: {tribunal} | Classe: {classe}")
                parts.append(f"   Assunto: {assunto}")
                parts.append(f"   Polo do investigado: {polo} | Status: {status}")
                parts.append(f"   Data de início: {data}")
                parts.append("")
        else:
            parts.append("**Processos Judiciais:** Nenhum processo estruturado identificado nas fontes.\n")

        # Decision excerpts
        decisao_chunks = [
            c for c in chunks
            if any(kw in c.get("text", "").lower()
                   for kw in ["acórdão", "sentença", "decisão", "provimento", "recurso", "condenação"])
        ]
        if decisao_chunks:
            parts.append("**Trechos de Decisões/Movimentações:**")
            for chunk in decisao_chunks[:3]:
                src_url = chunk.get("source_url", "")
                cite    = _cite(src_url, url_to_ref)
                parts.append(f"- {cite}\n  {chunk.get('text', '')[:500]}…")

        parts.append("\n**Alertas de Compliance:**")
        full_text = " ".join(c.get("text", "") for c in chunks).lower()
        alerts: list[str] = []
        if any(k in full_text for k in ["sanção", "ofac", "coaf"]):
            alerts.append("Possível presença em lista de sanções — verificar manualmente.")
        if any(k in full_text for k in ["pep", "politicamente exposta", "cargo público"]):
            alerts.append("Possível exposição como PEP (Pessoa Politicamente Exposta).")
        if any(k in full_text for k in ["fraude", "corrupção", "lavagem", "improbidade"]):
            alerts.append("Menção a termos de risco (fraude/corrupção/lavagem) nas fontes.")
        parts.extend(f"  ⚠ {a}" for a in alerts)
        if not alerts:
            parts.append("  Nenhum alerta de compliance identificado nas fontes consultadas.")

        return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Citation helpers
# ─────────────────────────────────────────────────────────────────────────────

_CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")


def _cite(url: str, url_to_ref: dict[str, int]) -> str:
    """Returns '[N]' if url has a numbered reference, else ''."""
    if not url:
        return ""
    num = url_to_ref.get(url.rstrip("/"))
    return f"[{num}]" if num else ""


def _chunk_cite(chunks: list[dict], domain_substr: str, url_to_ref: dict[str, int]) -> str:
    """Returns the [N] citation for the first chunk whose source_url contains domain_substr."""
    for c in chunks:
        url = c.get("source_url", "") or ""
        if domain_substr in url:
            return _cite(url, url_to_ref)
    return ""


def _normalize_role(role: str) -> str:
    mapping = {
        "IS_SOCIO": "Sócio",
        "WORKS_AT": "Funcionário/Vínculo empregatício",
    }
    return mapping.get(role, role or "Sócio")


def _name_in_qsa(target: str, qsa_matches: list[tuple]) -> bool:
    t_words = [w.lower() for w in target.split() if len(w) > 3]
    for q_name, _ in qsa_matches:
        q_lower = q_name.lower()
        if sum(1 for w in t_words if w in q_lower) >= 2:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Text extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_field(text: str, patterns: list[str]) -> str:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def _extract_primary_location(text: str) -> str:
    cities = [
        "Goiânia", "Goiania", "Brasília", "Brasilia", "São Paulo", "Rio de Janeiro",
        "Belo Horizonte", "Salvador", "Fortaleza", "Curitiba", "Manaus", "Recife",
        "Porto Alegre", "Belém", "Campinas", "Anápolis", "Aparecida de Goiânia",
        "Luziânia", "Senador Canedo",
    ]
    clean = re.sub(
        r"(?:TRT|Tribunal[^\n]{0,20})(?:[^\n]{0,60}São Paulo|São Paulo[^\n]{0,60}TRT)[^\n]*",
        "", text, flags=re.IGNORECASE,
    )
    clean = re.sub(r"15ª\s+Região[^\n]*", "", clean, flags=re.IGNORECASE)

    counts: dict[str, int] = {}
    for city in cities:
        n = len(re.findall(re.escape(city), clean, re.IGNORECASE))
        if n:
            counts[city] = n

    for city in ["Goiânia", "Goiania", "Anápolis", "Aparecida de Goiânia"]:
        if city in counts:
            counts[city] *= 3
    if re.search(r"\bGO\b|\bGoiás\b|\bGoias\b", clean, re.IGNORECASE):
        for city in ["Goiânia", "Goiania"]:
            counts[city] = counts.get(city, 0) + 5

    if not counts:
        m = re.search(r"([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+)*),\s*([A-Z]{2})\b", clean)
        if m:
            return f"{m.group(1)}, {m.group(2)}"
        return ""
    return max(counts, key=counts.get)


def _extract_formacao(text: str) -> list[str]:
    results: list[str] = []
    patterns = [
        r"(Graduação|Bacharelado|Licenciatura)\s+em\s+([^;,\n]{5,60}?)\s+(?:pela|pelo|na|no)\s+([^;,\n(]{5,60}?)\s*(?:\((\d{4})\)|\b(\d{4})\b)",
        r"(Especialização|MBA|Pós-Graduação)\s+em\s+([^;,\n]{5,60}?)\s+(?:pela|pelo|na|no)\s+([^;,\n(]{5,60}?)\s*(?:\((\d{4})\)|\b(\d{4})\b)",
        r"(Mestrado|Doutorado|Ph\.?D\.?)\s+em\s+([^;,\n]{5,60}?)\s+(?:pela|pelo|na|no)\s+([^;,\n(]{5,60}?)\s*(?:\((\d{4})\)|\b(\d{4})\b)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            g    = m.groups()
            nivel, curso, inst, ano = g[0], g[1].strip(), g[2].strip(), g[3] or (g[4] if len(g) > 4 else "")
            entry = f"{nivel} em {curso} — {inst}" + (f" ({ano})" if ano else "")
            if entry not in results:
                results.append(entry)
    return results[:6]


def _filter_chunks_by_target(chunks: list[dict], target: str) -> list[dict]:
    if not target:
        return chunks
    words = [w for w in target.split() if len(w) > 3]
    if len(words) < 2:
        return chunks
    relevant = [c for c in chunks if sum(1 for w in words if w.lower() in c.get("text","").lower()) >= 2]
    return relevant if relevant else chunks


def _extract_vinculos_atuais(text: str, target: str = "") -> list[str]:
    results: list[str] = []
    t_words = [w for w in (target or "").split() if len(w) > 3]
    patterns = [
        r"(Representante|Diretor|Diretor Geral|Professor|Presidente|Gerente|Coordenador)[^\n,;]{0,30}(?:junto|em|da|do|na|no)\s+([A-ZÀ-Ú][^\n,;]{3,60}?)(?:,|\s*\(|\s*desde|\n)",
        r"(?:cargo|função|vínculo)[:\s]+([A-ZÀ-Ú][^\n,;]{3,60}?)(?:,|\s*\(|\n)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            start, end = m.start(), m.end()
            ctx = text[max(0, start - 200): end + 200]
            if t_words:
                if sum(1 for w in t_words if w.lower() in ctx.lower()) < 2:
                    continue
            entry = " ".join(g for g in m.groups() if g).strip()
            if entry and entry not in results:
                results.append(entry)
    return results[:5]


def _extract_historico_profissional(text: str) -> list[str]:
    results: list[str] = []
    patterns = [
        r"([A-ZÀ-Ú][A-Za-zÀ-ú\s/&.-]{3,60}?)\s*[-–]\s*([A-Za-zÀ-ú\s]{3,40}?)\s*\((\d{4})\s*[-–]\s*(\d{4})\)",
        r"([A-ZÀ-Ú][A-Za-zÀ-ú\s/&.-]{3,60}?)\s*[-–]\s*([A-Za-zÀ-ú\s]{3,40}?)\s*\((\d{4})\s*[-–]\s*(?:atual|presente|atualidade)\)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            g      = m.groups()
            empresa = g[0].strip()
            cargo   = g[1].strip()
            periodo = f"{g[2]}-{g[3]}" if len(g) == 4 else f"{g[2]}-atual"
            entry   = f"{empresa} — {cargo} ({periodo})"
            if entry not in results and len(empresa) > 3 and len(cargo) > 2:
                results.append(entry)
    return results[:10]


def _extract_publicacoes(text: str) -> list[str]:
    results: list[str] = []
    patterns = [
        r'"([^"]{10,100})"\s*,\s*([^,\n]{5,60}),\s*(?:p\.\s*[\d-]+,\s*)?(\d{4})',
        r'([A-ZÀ-Ú][^,\n]{10,80}),\s*(Jornal|Revista|Caderno|Boletim)[^,\n]{0,40},\s*(\d{4})',
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            entry = "; ".join(g.strip() for g in m.groups() if g)
            if entry not in results:
                results.append(entry)
    return results[:5]


def _extract_premios(text: str) -> list[str]:
    results: list[str] = []
    patterns = [
        r"(Prêmio|Comenda|Medalha|Homenagem|Título)[:\s]+([^,\n]{5,80}),\s*([^,\n]{5,60}),\s*(\d{4})",
        r"(Comendador[a]?|Benemérito[a]?)\s+([^,\n]{5,60}),\s*([^,\n]{5,60}),\s*(\d{4})",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            entry = " ".join(g.strip() for g in m.groups() if g)
            if entry not in results:
                results.append(entry)
    return results[:5]


def _extract_dissertacoes(text: str) -> list[str]:
    results: list[str] = []
    patterns = [
        r'"([^"]{10,120})"\s*,\s*(Dissertação|Tese)[^,\n]*,\s*([^,\n]{5,60}),\s*(\d{4})',
        r'(Dissertação|Tese)[:\s]+"?([^",\n]{10,120})"?\s*,\s*([^,\n]{5,60}),\s*(\d{4})',
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            entry = "; ".join(g.strip() for g in m.groups() if g)
            if entry not in results:
                results.append(entry)
    return results[:3]


def _count_homonyms(text: str) -> int:
    m = re.search(r"(\d+)\s+homônim[oa]", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 1 if re.search(r"homônim[oa]", text, re.IGNORECASE) else 0
