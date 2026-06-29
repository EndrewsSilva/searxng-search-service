import re


class AntiBotDetector:

    # Mínimo de conteúdo útil esperado numa página com dados reais
    MIN_CONTENT_LENGTH = 500

    # Padrões que indicam DEFINITIVAMENTE uma página de desafio/bloqueio
    HARD_BLOCK_PATTERNS = [
        r"checking your browser",
        r"please verify you are a human",
        r"please complete the security check",
        r"enable javascript and cookies to continue",
        r"<title>\s*just a moment",          # Cloudflare challenge title
        r"<title>\s*attention required",     # Cloudflare WAF
        r"cf-challenge-form",                # Cloudflare challenge form
        r"id=['\"]challenge-form['\"]",
        r"hcaptcha\.com/1/api\.js",          # hCaptcha script
        r"recaptcha/api\.js",                # reCAPTCHA script
        r"403 forbidden",
        r"access denied",
        r"erro ao resolver",
    ]

    @staticmethod
    def is_blocked(html_content: str) -> bool:
        """
        Verifica se o HTML retornado é uma tela de bloqueio real.
        Mais específico: ignora menções genéricas a 'cloudflare' ou 'ddos'
        que aparecem em todo site que usa o CDN da Cloudflare.
        """
        if not html_content:
            return True

        if len(html_content) < AntiBotDetector.MIN_CONTENT_LENGTH:
            return True

        html_lower = html_content.lower()

        for pattern in AntiBotDetector.HARD_BLOCK_PATTERNS:
            if re.search(pattern, html_lower):
                return True

        return False
