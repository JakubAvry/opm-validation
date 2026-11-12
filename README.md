# OPM validator

Validace OPL (Object-Process Language, ISO 19450:2024) pravidly SHACL. Každé hlášení má kód pravidla, řádek
a sloupec, větu, článek normy a radu k opravě.

```
OPL → parser (Lark, gramatika podle Annex A) → RDF (ontologie podle Annex C) → SHACL (pySHACL) → hlášení
```

- `katalog/pravidla.yaml`: jediný zdroj pravidel (509 záznamů, každé „shall“ a „should“ normy); generují se z něj
  tvary SHACL (`opmvalid/tvary.ttl`) i texty hlášení.
- Kontrola: gramatika (115 pravidel) a SHACL (117 pravidel ve 51 tvarech: 30 Core, 21 SPARQL).
- Profily `iso` (výchozí) a `opcloud` (znění vět OPCloudu a jeho dvě pravidla navíc).
- Závažnost: E (shall), W (should), I (informace).

Popis, doklad správnosti a seznam tvarů a nálezů v normě: [`zprava/OPL_SHACL_validation.pdf`](zprava/OPL_SHACL_validation.pdf).

## Spuštění

Python 3.10+, `pip install -r requirements.txt`.

```
python -m opmvalid.web [--port 8766] [--no-browser]
python -m opmvalid model.opl [--profile iso|opcloud] [--format text|json] [--humans "A, B"] [--turtle out.ttl]
python katalog/pravidlo.py 9-036 [--run]      # jedno pravidlo: katalog, tvar SHACL, testy
```

- `--humans`: kdo v modelu je člověk (pravidla o agentovi a nástroji); bez něj se tato pravidla nekontrolují.
- Návratový kód: 0 bez chyb, 1 chyby, 2 vstup nejde přečíst.
- API: `POST /api/check`, JSON `{"text": "...", "profile": "iso", "humans": ["Operator"]}` → hlášení jako JSON.

## Testy

```
python testy/vse.py [--jena]
```

Platné věty (`testy/vety_platne.txt`), neplatné modely s očekávanými kódy, jeden na každý tvar SHACL
(`testy/vety_neplatne.txt`), model z nové domény, web proti CLI, převod do RDF, 18 modelů z OPCloudu
(`testy/reference/`).

Volitelné, jinak se přeskočí:
- `katalog/pokryti.py` a citace normy v `pravidlo.py` čtou text normy po stranách (`iso/sNNN.txt`, v `.gitignore`).
  Z PDF normy ho vytvoří `pip install pypdf` a `python katalog/iso_text.py <ISO_19450_2024.pdf>`; jinou cestu dá
  `OPM_ISO_PAGES`.
- `--jena` srovná pySHACL s Apache Jena 5.2.0; potřebuje `nastroje/jdk-17.0.21+9-jre/` (Temurin JRE 17)
  a `nastroje/apache-jena-5.2.0/`.

## Struktura

```
opmvalid/   parser.py, opl.lark, rdf.py, ontologie.ttl, tvary.ttl, validate.py, syntax.py, web.py, __main__.py
katalog/    pravidla.yaml, tvary.py (generuje tvary SHACL), pravidlo.py, pokryti.py, iso_text.py
testy/      testy a jejich data
zprava/     zpráva v PDF
```
