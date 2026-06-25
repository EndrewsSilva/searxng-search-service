"""
Script de teste para autenticação JusBrasil.

Uso:
    python test_jusbrasil_auth.py email@exemplo.com suasenha
"""

import asyncio
import sys
import re


async def main():
    if len(sys.argv) < 3:
        print("Uso: python test_jusbrasil_auth.py <email> <senha>")
        print("      python test_jusbrasil_auth.py email@exemplo.com suasenha")
        sys.exit(1)

    email = sys.argv[1]
    password = sys.argv[2]

    from app.infra.search.jusbrasil_session import JusBrasilSession

    session = JusBrasilSession(
        flaresolverr_url="http://localhost:8191",
        email=email,
        password=password,
    )

    print(f"\n{'='*60}")
    print("TESTE DE AUTENTICAÇÃO JUSBRASIL")
    print(f"{'='*60}\n")

    # Testa login
    success = await session.authenticate()

    if not success:
        print("\nFALHA: Autenticação não concluída.")
        sys.exit(1)

    print(f"\nCookies obtidos ({len(session._cookies)}):")
    for name, value in session._cookies.items():
        print(f"  {name}: {value[:40]}...")

    # Testa busca autenticada
    query = "João Silva"
    import urllib.parse
    search_url = f"https://www.jusbrasil.com.br/busca?q={urllib.parse.quote(query)}"

    print(f"\n{'='*60}")
    print(f"TESTANDO BUSCA AUTENTICADA")
    print(f"URL: {search_url}")
    print(f"{'='*60}\n")

    html = await session.get_page(search_url)

    print(f"HTML retornado: {len(html)} chars")

    if not html:
        print("AVISO: HTML vazio — autenticação pode não ter funcionado.")
        sys.exit(1)

    # Conta números CNJ
    cnj_pattern = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
    cnj_numbers = cnj_pattern.findall(html)

    print(f"Números CNJ encontrados no HTML: {len(cnj_numbers)}")
    for n in cnj_numbers[:5]:
        print(f"  {n}")

    # Links de processo
    from app.infra.search.process_link_extractor import ProcessLinkExtractor
    links = ProcessLinkExtractor.extract(html, search_url)
    print(f"\nLinks de processo extraídos: {len(links)}")
    for link in links[:5]:
        print(f"  {link}")

    if cnj_numbers or links:
        print("\nSUCESSO: Autenticação funcionou e conteúdo foi extraído!")
    else:
        print("\nAVISO: Autenticado mas sem processos encontrados para a query de teste.")
        print("Tente com um nome real que tenha processos no JusBrasil.")


if __name__ == "__main__":
    asyncio.run(main())
