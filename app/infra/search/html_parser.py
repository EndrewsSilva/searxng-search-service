from bs4 import BeautifulSoup
import re


class HTMLContentParser:

    @staticmethod
    def clean_html_to_text(html_content: str) -> str:

        if not html_content:
            return ""

        if "Erro ao carregar" in html_content:
            return ""

        soup = BeautifulSoup(
            html_content,
            "html.parser"
        )

        for tag in soup([
            "script",
            "style",
            "nav",
            "footer",
            "header",
            "noscript",
            "svg",
            "iframe",
            "form"
        ]):
            tag.decompose()

        text = soup.get_text(
            separator="\n",
            strip=True
        )

        text = re.sub(
            r"\n+",
            "\n",
            text
        )

        text = re.sub(
            r"[ \t]+",
            " ",
            text
        )

        blacklist = [
            "Entrar",
            "Cadastrar",
            "Menu",
            "Política de Cookies",
            "Aceitar Cookies",
            "Faça Login",
            "Anunciar",
        ]

        lines = []

        for line in text.split("\n"):

            line = line.strip()

            if not line:
                continue

            if any(
                blocked.lower() in line.lower()
                for blocked in blacklist
            ):
                continue

            lines.append(line)

        return "\n".join(lines)