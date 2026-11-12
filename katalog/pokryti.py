"""Pokrytí normy katalogem: každé „shall“ a „should“ v ISO 19450 musí mít v katalogu záznam.

Spočítá slova shall / should v textu normy po článcích a porovná je s poli `covers` záznamů v katalog/pravidla.yaml.
Vypíše tabulku po článcích a rozpad záznamů podle kategorie a způsobu kontroly.

Text normy (ISO 19450:2024 po stranách PDF, soubory sNNN.txt) není součástí repozitáře: čte se ze složky iso/
(v .gitignore; vytvoří ji katalog/iso_text.py z PDF normy), jinou cestu zadá volba --iso nebo proměnná OPM_ISO_PAGES. Bez textu normy skript napíše, že
kontrolu přeskakuje, a skončí kódem 0.

Použití:
  python katalog/pokryti.py 9.1          články začínající 9.1
  python katalog/pokryti.py              celá norma (přehled: kolik článků je hotových)
  --iso <složka strany/>                 text normy po stranách
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
# text of the norm, not in the repository: OPM_ISO_PAGES, else the folder iso/ (in .gitignore)
ISO_PAGES = (Path(os.environ["OPM_ISO_PAGES"]) if os.environ.get("OPM_ISO_PAGES")
             else HERE.parent / "iso")
FIRST_PAGE = 7          # strana PDF s článkem 1 (předtím obsah a předmluva)
HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+([A-Z][A-Za-z].*)$")
ANNEX = re.compile(r"^\s*Annex ([A-D])\s*$")      # nadpis přílohy stojí na řádce sám (odkazy v textu ne)
WORD = {"shall": re.compile(r"\bshall\b", re.I), "should": re.compile(r"\bshould\b", re.I)}
CATEGORY = {1: "syntax", 2: "structure/semantics", 3: "OPD graphics", 4: "runtime", 5: "definition"}


def _key(clause: str) -> tuple:
    return tuple(int(x) for x in clause.split("."))


def _accept(new: str, cur: str | None) -> bool:
    """Nadpis článku jen tehdy, když navazuje na předchozí (vyřadí čísla v obrázcích a tabulkách)."""
    if cur is None:
        return new == "1"
    if not cur[0].isdigit():
        return False                       # za přílohami už žádné číslované články
    n, c = _key(new), _key(cur)
    if n <= c:
        return False
    if len(n) == 1:
        return n[0] == c[0] + 1
    parent = n[:-1]
    # další článek na téže úrovni, první podčlánek, nebo návrat na vyšší úroveň
    return (parent == c[:len(parent)] and n[-1] == (c[len(parent)] + 1 if len(c) > len(parent) else 1)) \
        or (len(n) <= len(c) and n[:-1] == c[:len(n) - 1] and n[-1] == c[len(n) - 1] + 1)


def iso_counts(pages: Path) -> dict[str, Counter]:
    counts: dict[str, Counter] = defaultdict(Counter)
    cur = None
    for f in sorted(pages.glob("s*.txt")):
        if int(f.stem[1:]) < FIRST_PAGE:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            m, a = HEADING.match(line), ANNEX.match(line)
            if a:
                cur = a.group(1)
            elif m and _accept(m.group(1), cur):
                cur = m.group(1)
            if cur is None:
                continue
            for w, rx in WORD.items():
                counts[cur][w] += len(rx.findall(line))
    return counts


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    pages = Path(argv[argv.index("--iso") + 1]) if "--iso" in argv else ISO_PAGES
    if not pages.is_dir() or not any(pages.glob("s*.txt")):
        print(f"Text normy ISO 19450 není k dispozici ({pages}): kontrola pokrytí se přeskakuje. "
              "Cestu k textu po stranách zadej volbou --iso nebo proměnnou OPM_ISO_PAGES.")
        return 0
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--iso")]
    prefix = args[0] if args else ""
    cat = yaml.safe_load((HERE / "pravidla.yaml").read_text(encoding="utf-8"))
    iso = iso_counts(pages)
    covered: dict[str, Counter] = defaultdict(Counter)
    for r in cat["rules"]:
        if str(r["iso"]) != "-":                # pravidla OPCloudu nemají článek normy
            covered[str(r["iso"])][r["strength"]] += r.get("covers", 1)

    def inside(c: str) -> bool:
        return not prefix or c == prefix or c.startswith(prefix + ".")

    clauses = sorted({c for c in iso if inside(c)} | {c for c in covered if inside(c)},
                     key=lambda c: (not c[0].isdigit(), _key(c) if c[0].isdigit() else (ord(c[0]),)))
    if not prefix:
        tot_iso = sum(iso[c]["shall"] for c in clauses)
        done = [c for c in clauses if iso[c]["shall"] and covered[c]["shall"] == iso[c]["shall"]]
        print(f"Norma: {tot_iso} × shall, {sum(iso[c]['should'] for c in clauses)} × should v {len(clauses)} článcích.")
        print(f"Pokryto v katalogu: {sum(covered[c]['shall'] for c in clauses)} × shall; "
              f"úplně hotových článků se shall {len(done)} z {sum(1 for c in clauses if iso[c]['shall'])}.")
        return 0
    print(f"| článek | shall v ISO | v katalogu | should v ISO | v katalogu | stav |")
    print("|---|---|---|---|---|---|")
    ok = True
    for c in clauses:
        i, k = iso[c], covered[c]
        state = "ok" if (i["shall"], i["should"]) == (k["shall"], k["should"]) else "CHYBÍ" \
            if k["shall"] + k["should"] < i["shall"] + i["should"] else "NAVÍC"
        ok &= state == "ok"
        print(f"| {c} | {i['shall']} | {k['shall']} | {i['should']} | {k['should']} | {state} |")
    rules = [r for r in cat["rules"] if inside(str(r["iso"]))]
    print()
    print(f"Záznamů {len(rules)}. Podle kategorie: "
          + ", ".join(f"{n} {CATEGORY[c]}" for c, n in sorted(Counter(r["category"] for r in rules).items())))
    by = Counter(r["check"]["by"] for r in rules)
    core = sum(1 for r in rules if r["check"]["by"] == "shacl" and r["check"].get("core"))
    print(f"Kontrola: gramatika {by['grammar']}, SHACL {by['shacl']} (z toho jádro {core}), nic {by['none']}.")
    # záznam se sdíleným tvarem jen odkazuje na tvar jiného záznamu ("Shared shape of …")
    shapes = [r["id"] for r in rules if r["check"]["by"] == "shacl"
              and not str(r["check"].get("plan", "")).startswith("Shared")]
    blind = sum(1 for r in rules if r["category"] == 2 and r["check"]["by"] == "none")
    needs = [r["id"] for r in rules if r["check"].get("needs")]
    print(f"Různých tvarů SHACL {len(shapes)} ({', '.join(shapes)}); statických, ale z OPL nekontrolovatelných "
          f"{blind}; kontrolovatelných jen s údajem mimo OPL {len(needs)} ({', '.join(needs) or '-'}).")
    implicit = [r["id"] for r in rules if r["strength"] == "implicit"]
    if implicit:
        print(f"Požadavků bez „shall“ (mimo počet): {len(implicit)} ({', '.join(implicit)}).")
    print(f"Nálezů v normě v katalogu celkem {len(cat.get('findings', []))}.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
