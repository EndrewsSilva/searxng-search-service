import json

with open("resultado.json", encoding="utf-8") as f:
    data = json.load(f)

print("\n" + "=" * 100)
print("INVESTIGADO")
print("=" * 100)
print(data["query"])

print("\n" + "=" * 100)
print("PROCESSOS ENCONTRADOS")
print("=" * 100)

for i, process in enumerate(data["process_entities"], start=1):
    print(f"\nPROCESSO {i}")
    print("-" * 80)

    print("Título:")
    print(process["title"])

    print("\nTribunal:")
    print(process["court"])

    print("\nNúmero(s):")
    for p in process["process_numbers"]:
        print(" -", p)

    print("\nPartes:")
    print(process["parties"])

    print("\nLocal:")
    print(process["origin_location"])

    print("\nURL:")
    print(process["source_url"])
