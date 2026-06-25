import re

class AntiBotDetector:

    @staticmethod
    def is_blocked(html_content: str) -> bool:
        """
        Verifica se o HTML retornado é uma tela de bloqueio do Cloudflare ou CAPTCHA.
        """
        if not html_content or "Erro ao resolver" in html_content:
            return True

        # Padrões textuais que indicam que fomos barrados por um sistema de segurança
        block_patterns = [
            r"ddos",
            r"cloudflare",
            r"captcha",
            r"hcaptcha",
            r"recaptcha",
            r"checking your browser",
            r"access denied",
            r"403 forbidden",
            r"please verify you are a human"
        ]

        html_lower = html_content.lower()
        for pattern in block_patterns:
            if re.search(pattern, html_lower):
                return True

        return False
