import re

from bs4 import BeautifulSoup


class ProcessEntityExtractor:

    PROCESS_NUMBER_PATTERN = re.compile(
        r"\b(?:\d{7}|[0-9Xx]{7})-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b",
        re.IGNORECASE,
    )

    @classmethod
    def extract(cls, html: str, source_url: str) -> dict:
        entities = cls.extract_many(html, source_url)

        if entities:
            return entities[0]

        return cls._empty_entity(html, source_url)

    @classmethod
    def extract_many(cls, html: str, source_url: str) -> list[dict]:
        soup = BeautifulSoup(html or "", "html.parser")
        text = soup.get_text(" ", strip=True)

        title = cls._extract_title(soup)
        description = cls._extract_description(soup)

        # JusBrasil (Next.js) embeds all data in __NEXT_DATA__ — invisible to get_text()
        next_data_text = cls._extract_next_data_text(html or "")
        searchable_text = f"{title} {description} {text} {next_data_text}"

        blocks = cls._split_process_blocks(searchable_text)

        entities = []

        for block in blocks:
            process_numbers = cls.PROCESS_NUMBER_PATTERN.findall(block)

            if not process_numbers:
                continue

            block_title = cls._extract_block_title(block, title)
            parties = cls._extract_parties(block_title) or cls._extract_parties_from_text(block)

            entities.append(
                {
                    "source_url": cls._extract_process_url(block, source_url),
                    "title": block_title,
                    "description": description,
                    "process_numbers": list(dict.fromkeys(process_numbers)),
                    "has_process_number": True,
                    "status": cls._extract_status(block),
                    "court": cls._extract_court(block, source_url),
                    "subject": cls._extract_subject(block),
                    "case_value": cls._extract_after_label(block, "Valor da causa"),
                    "last_check": cls._extract_after_label(block, "Última verificação"),
                    "origin_location": cls._extract_origin_location(block),
                    "origin_date": cls._extract_origin_date(block),
                    "parties": parties,
                    "movements_count": cls._extract_movements_count(block),
                    "text_preview": block[:4000],
                }
            )

        if entities:
            return cls._deduplicate_entities(entities)

        process_numbers = cls.PROCESS_NUMBER_PATTERN.findall(searchable_text)

        if not process_numbers:
            return []

        parties = cls._extract_parties(title) or cls._extract_parties_from_text(searchable_text)

        return [
            {
                "source_url": source_url,
                "title": title,
                "description": description,
                "process_numbers": list(dict.fromkeys(process_numbers)),
                "has_process_number": bool(process_numbers),
                "status": cls._extract_status(searchable_text),
                "court": cls._extract_court(searchable_text, source_url),
                "subject": cls._extract_subject(searchable_text),
                "case_value": cls._extract_after_label(searchable_text, "Valor da causa"),
                "last_check": cls._extract_after_label(searchable_text, "Última verificação"),
                "origin_location": cls._extract_origin_location(searchable_text),
                "origin_date": cls._extract_origin_date(searchable_text),
                "parties": parties,
                "movements_count": cls._extract_movements_count(searchable_text),
                "text_preview": text[:4000],
            }
        ]

    @classmethod
    def _empty_entity(cls, html: str, source_url: str) -> dict:
        soup = BeautifulSoup(html or "", "html.parser")
        title = cls._extract_title(soup)
        description = cls._extract_description(soup)
        text = soup.get_text(" ", strip=True)

        return {
            "source_url": source_url,
            "title": title,
            "description": description,
            "process_numbers": [],
            "has_process_number": False,
            "status": "",
            "court": "",
            "subject": "",
            "case_value": "",
            "last_check": "",
            "origin_location": "",
            "origin_date": "",
            "parties": {},
            "movements_count": None,
            "text_preview": text[:4000],
        }

    @classmethod
    def _split_process_blocks(cls, text: str) -> list[str]:
        matches = list(cls.PROCESS_NUMBER_PATTERN.finditer(text))

        if not matches:
            return []

        blocks = []

        for index, match in enumerate(matches):
            start = max(0, match.start() - 500)
            end = (
                min(len(text), matches[index + 1].start() + 500)
                if index + 1 < len(matches)
                else min(len(text), match.end() + 1200)
            )

            blocks.append(text[start:end])

        return blocks

    @classmethod
    def _extract_block_title(cls, block: str, fallback_title: str) -> str:
        patterns = [
            r"([A-ZÁÉÍÓÚÂÊÔÃÕÇA-Za-zÀ-ÿ0-9\s\.\-&/]+?\s+x\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇA-Za-zÀ-ÿ0-9\s\.\-&/]+?)\s+-?\s*Processo\s+n[º°]",
            r"([A-ZÁÉÍÓÚÂÊÔÃÕÇA-Za-zÀ-ÿ0-9\s\.\-&/]+?\s+x\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇA-Za-zÀ-ÿ0-9\s\.\-&/]+?)\s+N[°º]",
        ]

        for pattern in patterns:
            match = re.search(pattern, block, re.IGNORECASE)

            if match:
                return match.group(1).strip(" .,-") + " - Processo"

        number = cls.PROCESS_NUMBER_PATTERN.search(block)

        if number:
            return f"{fallback_title} - Processo nº {number.group(0)}"

        return fallback_title

    @classmethod
    def _extract_process_url(cls, block: str, fallback_url: str) -> str:
        match = re.search(
            r"https?://[^\s\"'>]+(?:processos|processos-judiciais)[^\s\"'>]+",
            block,
            re.IGNORECASE,
        )

        if match:
            return match.group(0).strip(" .,)")

        return fallback_url

    @staticmethod
    def _extract_next_data_text(html: str) -> str:
        match = re.search(
            r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        return match.group(1) if match else ""

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        for attrs in (
            {"property": "og:title"},
            {"name": "twitter:title"},
        ):
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                return tag["content"].strip()

        if soup.title and soup.title.string:
            return soup.title.string.strip()

        h1 = soup.find("h1")
        if h1:
            return h1.get_text(" ", strip=True)

        return ""

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> str:
        for attrs in (
            {"property": "og:description"},
            {"name": "description"},
            {"name": "twitter:description"},
        ):
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                return tag["content"].strip()

        return ""

    @staticmethod
    def _extract_after_label(text: str, label: str, fallback: str = "") -> str:
        pattern = (
            rf"{re.escape(label)}\s*[:\-]?\s*(.+?)"
            rf"(?:\s+(?:Origem|Assunto|Instância|Envolvidos|Valor da causa|Última verificação|Ver processo|Processo|Polo|Autor|Réu|Requerente|Requerido)\b|$)"
        )

        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            return match.group(1).strip(" .,-")

        return fallback

    @staticmethod
    def _extract_status(text: str) -> str:
        patterns = [
            "Processo ativo",
            "Processo arquivado",
            "Processo extinto",
            "Processo suspenso",
            "Processo baixado",
            "Processo inativo",
        ]

        text_lower = text.lower()

        for pattern in patterns:
            if pattern.lower() in text_lower:
                return pattern

        return ""

    @staticmethod
    def _extract_subject(text: str) -> str:
        patterns = [
            r"Assunto\s+(.+?)\s+Origem",
            r"Assunto\s+(.+?)\s+Instância",
            r"Assunto\s+(.+?)\s+Envolvidos",
            r"Assunto\s+(.+?)\s+Ver processo",
            r"Assunto\s+(.+?)\s+Valor da causa",
            r"CLASSE PROCESSUAL\s*:\s*(.+?)\s+POLO",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)

            if match:
                return match.group(1).strip(" .,-")

        return ""

    @staticmethod
    def _extract_court(text: str, source_url: str = "") -> str:
        source = f"{text} {source_url}"

        patterns = [
            r"\b(TRT[- ]?\d{1,2})\b",
            r"\b(TRF[- ]?\d{1,2})\b",
            r"\b(TJ[A-Z]{2})\b",
            r"\b(TRE[- ]?[A-Z]{2})\b",
            r"\b(TST)\b",
            r"\b(STJ)\b",
            r"\b(STF)\b",
            r"\b(TSE)\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, source, re.IGNORECASE)

            if match:
                return match.group(1).upper().replace(" ", "-")

        return ""

    @staticmethod
    def _extract_origin_location(text: str) -> str:
        patterns = [
            r"·\s*([A-Za-zÀ-ÿ\s\.\-]+,\s*[A-Z]{2})\s+N[°º]",
            r"\b(?:TJ[A-Z]{2}|TRT[- ]?\d{1,2}|TRF[- ]?\d{1,2}|TRE[- ]?[A-Z]{2})\s*·\s*([A-Za-zÀ-ÿ\s\.\-]+,\s*[A-Z]{2})",
            r"Foro\s*·\s*([A-Za-zÀ-ÿ\s\.\-]+(?:,\s*[A-Za-zÀ-ÿ\s\.\-]+)*,\s*[A-Z]{2})",
            r"LOCAL\s*:\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ\s\.\-]+(?:,\s*[A-ZÁÉÍÓÚÂÊÔÃÕÇ\s\.\-]+)*,\s*[A-Z]{2})",
            r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÀ-ÿ\s\.\-]+,\s*[A-Z]{2})\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)

            if match:
                return match.group(1).strip(" .,-")

        return ""

    @staticmethod
    def _extract_origin_date(text: str) -> str:
        patterns = [
            r"Iniciado em\s+(\d{4})",
            r"Última movimentação\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
            r"publicado em\s+(\d{1,2}/\d{1,2}/\d{4})",
            r"Data da Movimentação\s+(\d{1,2}/\d{1,2}/\d{4})",
            r"Data da Movimenta[çc][ãa]o\s+(\d{1,2}/\d{1,2}/\d{4})",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)

            if match:
                return match.group(1).strip()

        return ""

    @staticmethod
    def _extract_parties(title: str) -> dict:
        clean_title = title.split(" - Processo")[0].strip()
        clean_title = clean_title.split(" | ")[0].strip()

        separators = [" x ", " X ", " vs ", " VS ", " contra "]

        for separator in separators:
            if separator in clean_title:
                left, right = clean_title.split(separator, 1)

                return {
                    "left": left.strip(),
                    "right": right.strip(),
                }

        return {}

    @staticmethod
    def _extract_parties_from_text(text: str) -> dict:
        patterns = [
            r"(.+?)\s+x\s+(.+?)\s+Processo\s+n[º°]",
            r"POLO ATIVO\s*:?\s*(.+?)\s+POLO PASSIVO\s*:?\s*(.+?)(?:\s+SEGREDO|\s+PARTE|\s+ADVGS|\s+Certidão|\s+Decisão|$)",
            r"Autor\s+(.+?)\s+Assunto\s+.+?\s+Envolvidos\s+(.+?)(?:\s+Ver processo|$)",
            r"Requerente\s+(.+?)\s+Assunto\s+.+?\s+Envolvidos\s+(.+?)(?:\s+Ver processo|$)",
            r"Executado\s+(.+?)\s+Assunto\s+.+?\s+Envolvidos\s+(.+?)(?:\s+Ver processo|$)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)

            if match:
                left = match.group(1).strip(" .,-")
                right = match.group(2).strip(" .,-")

                if left and right:
                    return {
                        "left": left,
                        "right": right,
                    }

        return {}

    @staticmethod
    def _extract_movements_count(text: str) -> int | None:
        patterns = [
            r"Movimentações\s+\((\d+)\)",
            r"(\d+)\s+movimentações",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)

            if match:
                return int(match.group(1))

        return None

    @staticmethod
    def _deduplicate_entities(entities: list[dict]) -> list[dict]:
        seen = set()
        unique = []

        for entity in entities:
            numbers = entity.get("process_numbers") or []
            key = "|".join(numbers) if numbers else entity.get("source_url", "")

            if key in seen:
                continue

            seen.add(key)
            unique.append(entity)

        return unique