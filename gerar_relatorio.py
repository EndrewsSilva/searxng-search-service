import json


INPUT_FILE = "resultado.json"
OUTPUT_FILE = "relatorio_scraping.txt"


with open(INPUT_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)


with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write("POC - SearXNG Search Service + Scraping\n")
    f.write("=" * 80 + "\n\n")

    f.write(f"Query: {data.get('query')}\n")
    f.write(f"Total encontrado: {data.get('total_found')}\n\n")

    for i, result in enumerate(data.get("results", []), start=1):
        f.write("=" * 80 + "\n")
        f.write(f"RESULTADO {i}\n")
        f.write("=" * 80 + "\n\n")

        f.write(f"Título: {result.get('title')}\n")
        f.write(f"URL: {result.get('url')}\n")
        f.write(f"Snippet: {result.get('snippet')}\n\n")

        f.write("CONTEÚDO EXTRAÍDO / HTML_FULL:\n")
        f.write("-" * 80 + "\n")
        f.write(result.get("html_full", "Sem conteúdo extraído."))
        f.write("\n\n")

print(f"Relatório gerado em: {OUTPUT_FILE}")

