import asyncio
import logging
from typing import Optional

import httpx

from app.domain.models.search import ProcessEntity


logger = logging.getLogger(__name__)


class DataJudClient:
    """
    Cliente para a API pública do DataJud (CNJ).

    Registro gratuito em: https://api-publica.datajud.cnj.jus.br/
    Documentação: https://datajud-wiki.cnj.jus.br/

    Coloque a chave no .env:
        DATAJUD_API_KEY=ApiKey <sua_chave_aqui>
    """

    BASE_URL = "https://api-publica.datajud.cnj.jus.br"

    # Todos os tribunais disponíveis no DataJud
    ALL_TRIBUNALS = [
        # Justiça Estadual (27 TJs)
        "tjac", "tjal", "tjam", "tjap", "tjba", "tjce", "tjdft",
        "tjes", "tjgo", "tjma", "tjmg", "tjms", "tjmt", "tjpa",
        "tjpb", "tjpe", "tjpi", "tjpr", "tjrj", "tjrn", "tjro",
        "tjrr", "tjrs", "tjsc", "tjse", "tjsp", "tjto",
        # Justiça Federal (TRFs)
        "trf1", "trf2", "trf3", "trf4", "trf5",
        # Justiça do Trabalho (TRTs)
        "trt1", "trt2", "trt3", "trt4", "trt5", "trt6",
        "trt7", "trt8", "trt9", "trt10", "trt11", "trt12",
        "trt13", "trt14", "trt15", "trt16", "trt17", "trt18",
        "trt19", "trt20", "trt21", "trt22", "trt23", "trt24",
        # Superiores
        "stj", "tst",
    ]

    def __init__(self, api_key: str, max_concurrent: int = 15):
        self.api_key = api_key
        self.semaphore = asyncio.Semaphore(max_concurrent)

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=15.0, connect=5.0),
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
        )

    async def search_by_name(
        self,
        name: str,
        results_per_tribunal: int = 5,
        tribunals: Optional[list[str]] = None,
    ) -> list[ProcessEntity]:
        """
        Busca processos pelo nome da parte em todos os tribunais em paralelo.
        Retorna lista de ProcessEntity pronta para uso no pipeline.
        """
        target_tribunals = tribunals or self.ALL_TRIBUNALS

        tasks = [
            self._search_tribunal(tribunal, name, results_per_tribunal)
            for tribunal in target_tribunals
        ]

        responses = await asyncio.gather(*tasks, return_exceptions=True)

        entities: list[ProcessEntity] = []
        tribunals_with_results = 0

        for tribunal, result in zip(target_tribunals, responses):
            if isinstance(result, Exception):
                logger.debug(f"[DataJud] {tribunal}: {str(result)}")
                continue
            if result:
                entities.extend(result)
                tribunals_with_results += 1

        print(
            f"[DataJud] name='{name}' "
            f"tribunais_pesquisados={len(target_tribunals)} "
            f"tribunais_com_resultado={tribunals_with_results} "
            f"total_processos={len(entities)}"
        )

        return entities

    async def _search_tribunal(
        self, tribunal: str, name: str, size: int
    ) -> list[ProcessEntity]:
        url = f"{self.BASE_URL}/api_publica_{tribunal}/_search"

        payload = {
            "query": {
                "match": {
                    "nomePartes": {
                        "query": name,
                        "operator": "and",
                    }
                }
            },
            "size": size,
            "_source": [
                "numeroProcesso",
                "tribunal",
                "grau",
                "dataAjuizamento",
                "classe",
                "assuntos",
                "orgaoJulgador",
                "partes",
                "movimentos",
                "valor",
            ],
        }

        async with self.semaphore:
            try:
                response = await self.client.post(url, json=payload)

                if response.status_code == 404:
                    return []

                response.raise_for_status()

                hits = response.json().get("hits", {}).get("hits", [])

                return [
                    self._to_process_entity(hit["_source"], tribunal)
                    for hit in hits
                    if hit.get("_source")
                ]

            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (404, 503):
                    logger.debug(
                        f"[DataJud] {tribunal} HTTP {e.response.status_code}"
                    )
                return []

            except Exception as e:
                logger.debug(f"[DataJud] {tribunal}: {str(e)}")
                return []

    @staticmethod
    def _to_process_entity(raw: dict, tribunal_fallback: str) -> ProcessEntity:
        numero = raw.get("numeroProcesso", "")
        tribunal = (raw.get("tribunal") or tribunal_fallback).upper()

        partes = raw.get("partes") or []
        polo_ativo = [p["nome"] for p in partes if p.get("polo") == "ATIVO"]
        polo_passivo = [p["nome"] for p in partes if p.get("polo") == "PASSIVO"]

        assuntos = raw.get("assuntos") or []
        subject_names = ", ".join(a.get("nome", "") for a in assuntos[:3] if a.get("nome"))

        classe = raw.get("classe") or {}
        classe_nome = classe.get("nome", "")

        movimentos = raw.get("movimentos") or []
        ultimo_mov = ""
        if movimentos:
            ultimo = max(movimentos, key=lambda m: m.get("dataHora", ""), default=None)
            if ultimo:
                ultimo_mov = (ultimo.get("dataHora") or "")[:10]

        orgao = raw.get("orgaoJulgador") or {}

        data_ajuizamento = (raw.get("dataAjuizamento") or "")[:10]

        valor = raw.get("valor")
        case_value = ""
        if valor:
            try:
                case_value = f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            except (ValueError, TypeError):
                pass

        numero_clean = numero.replace(".", "").replace("-", "")
        source_url = (
            f"https://www.jusbrasil.com.br/processos/{numero_clean}"
            if numero_clean
            else f"datajud://{tribunal_fallback}/{numero}"
        )

        subject_full = " | ".join(filter(None, [classe_nome, subject_names]))

        return ProcessEntity(
            source_url=source_url,
            title=f"{classe_nome} – {numero}" if classe_nome else numero,
            description=subject_full,
            process_numbers=[numero] if numero else [],
            has_process_number=bool(numero),
            status="",
            court=tribunal,
            subject=subject_full,
            case_value=case_value,
            last_check=ultimo_mov,
            origin_location=orgao.get("nome", ""),
            origin_date=data_ajuizamento,
            parties={
                "polo_ativo": polo_ativo,
                "polo_passivo": polo_passivo,
            },
            movements_count=len(movimentos),
            text_preview=None,
            source="datajud",
        )

    async def close(self):
        await self.client.aclose()
