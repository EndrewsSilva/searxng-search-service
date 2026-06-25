import asyncio
import logging
import re
from typing import Optional

import httpx

from app.domain.models.search import ProcessEntity


logger = logging.getLogger(__name__)


# Mapeamento J.TT → índice DataJud, derivado do formato CNJ: NNNNNNN-DD.AAAA.J.TT.OOOO
_J8_TJ = {
    1: "tjac", 2: "tjal", 3: "tjap", 4: "tjam", 5: "tjba",
    6: "tjce", 7: "tjdft", 8: "tjes", 9: "tjgo", 10: "tjma",
    11: "tjmt", 12: "tjms", 13: "tjmg", 14: "tjpa", 15: "tjpb",
    16: "tjpe", 17: "tjpi", 18: "tjpr", 19: "tjrj", 20: "tjrn",
    21: "tjro", 22: "tjrr", 23: "tjrs", 24: "tjsc", 25: "tjse",
    26: "tjsp", 27: "tjto",
}

_CNJ_PATTERN = re.compile(
    r"(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})"
)


def _tribunal_from_cnj(numero: str) -> Optional[str]:
    """Deriva o índice DataJud a partir do número CNJ formatado."""
    m = _CNJ_PATTERN.search(numero)
    if not m:
        return None

    j, tt = int(m.group(4)), int(m.group(5))

    if j == 8:
        return _J8_TJ.get(tt)
    if j == 4:
        return f"trf{tt}" if 1 <= tt <= 6 else None
    if j == 5:
        return f"trt{tt}" if 1 <= tt <= 24 else None
    if j == 3:
        return "stj"
    if j == 1:
        return "stf"
    if j == 9:
        return "tjdft"

    return None


def _normalize_cnj(numero: str) -> str:
    """Remove separadores do número CNJ para busca no DataJud."""
    return re.sub(r"[.\-]", "", numero)


def _format_datajud_date(raw: str) -> str:
    """Converte 'YYYYMMDDHHmmss' para 'YYYY-MM-DD'."""
    raw = (raw or "").strip()
    if len(raw) >= 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw[:10]  # já está em ISO ou outro formato


class DataJudClient:
    """
    Cliente para a API pública do DataJud (CNJ).

    Chave pública (sem cadastro):
        APIKey cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw==

    Limitações da chave pública:
    - NÃO permite busca por nome de parte (nomePartes) — campo LGPD protegido
    - PERMITE buscar processo por número e obter dados estruturados completos

    Fluxo recomendado:
        1. Scraping (JusBrasil/Escavador) descobre números de processo
        2. DataJud enriquece cada número com dados oficiais do CNJ
    """

    BASE_URL = "https://api-publica.datajud.cnj.jus.br"
    PUBLIC_KEY = "APIKey cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="

    def __init__(self, api_key: Optional[str] = None, max_concurrent: int = 10):
        self.api_key = api_key or self.PUBLIC_KEY
        self.semaphore = asyncio.Semaphore(max_concurrent)

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=15.0, connect=5.0),
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
            },
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def enrich_by_numbers(
        self, process_numbers: list[str]
    ) -> list[ProcessEntity]:
        """
        Recebe números CNJ formatados (ex: '1234567-89.2023.8.26.0001'),
        determina o tribunal de cada um e consulta o DataJud.
        Retorna ProcessEntity com dados estruturados oficiais.
        """
        if not process_numbers:
            return []

        tasks = []
        meta = []  # (numero_original, tribunal) para log

        for numero in process_numbers:
            tribunal = _tribunal_from_cnj(numero)
            if not tribunal:
                logger.debug(f"[DataJud] Não foi possível determinar tribunal: {numero}")
                continue
            numero_clean = _normalize_cnj(numero)
            tasks.append(self._fetch_process(tribunal, numero_clean, numero))
            meta.append((numero, tribunal))

        if not tasks:
            return []

        responses = await asyncio.gather(*tasks, return_exceptions=True)

        entities = []
        found = 0
        for (numero, tribunal), result in zip(meta, responses):
            if isinstance(result, Exception):
                logger.debug(f"[DataJud] {tribunal}/{numero}: {str(result)}")
            elif result:
                entities.append(result)
                found += 1

        print(
            f"[DataJud] enrich: {len(process_numbers)} número(s) → "
            f"{found} processo(s) encontrado(s) no CNJ"
        )

        return entities

    async def _fetch_process(
        self, tribunal: str, numero_clean: str, numero_original: str
    ) -> Optional[ProcessEntity]:
        url = f"{self.BASE_URL}/api_publica_{tribunal}/_search"

        payload = {
            "query": {"term": {"numeroProcesso": numero_clean}},
            "size": 1,
        }

        async with self.semaphore:
            try:
                response = await self.client.post(url, json=payload)

                if response.status_code == 404:
                    return None

                response.raise_for_status()

                hits = response.json().get("hits", {}).get("hits", [])
                if not hits:
                    return None

                return self._to_process_entity(hits[0]["_source"], tribunal, numero_original)

            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (404, 503):
                    logger.debug(f"[DataJud] {tribunal} HTTP {e.response.status_code}")
                return None

            except Exception as e:
                logger.debug(f"[DataJud] {tribunal}/{numero_clean}: {str(e)}")
                return None

    @staticmethod
    def _to_process_entity(raw: dict, tribunal_fallback: str, numero_original: str) -> ProcessEntity:
        numero = numero_original
        tribunal = (raw.get("tribunal") or tribunal_fallback).upper()

        classe = raw.get("classe") or {}
        classe_nome = classe.get("nome", "")

        assuntos = raw.get("assuntos") or []
        subject_names = ", ".join(
            a.get("nome", "") for a in assuntos[:3] if a.get("nome")
        )

        orgao = raw.get("orgaoJulgador") or {}

        movimentos = raw.get("movimentos") or []
        ultimo_mov = ""
        if movimentos:
            ultimo = max(
                movimentos,
                key=lambda m: m.get("dataHora", ""),
                default=None,
            )
            if ultimo:
                ultimo_mov = (ultimo.get("dataHora") or "")[:10]

        data_ajuizamento = _format_datajud_date(raw.get("dataAjuizamento", ""))

        grau_map = {"G1": "1º Grau", "G2": "2º Grau", "JE": "Juizado Especial"}
        grau = grau_map.get(raw.get("grau", ""), raw.get("grau", ""))

        subject_full = " | ".join(filter(None, [classe_nome, subject_names, grau]))

        numero_clean = re.sub(r"[.\-]", "", numero)
        source_url = (
            f"https://www.jusbrasil.com.br/processos/{numero_clean}"
            if numero_clean
            else f"datajud://{tribunal_fallback}"
        )

        return ProcessEntity(
            source_url=source_url,
            title=f"{classe_nome} – {numero}" if classe_nome else numero,
            description=subject_full,
            process_numbers=[numero],
            has_process_number=True,
            status="",
            court=tribunal,
            subject=subject_full,
            case_value="",
            last_check=ultimo_mov,
            origin_location=orgao.get("nome", ""),
            origin_date=data_ajuizamento,
            parties={},
            movements_count=len(movimentos),
            text_preview=None,
            source="datajud",
        )

    async def close(self):
        await self.client.aclose()
