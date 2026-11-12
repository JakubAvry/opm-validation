"""Text normy ISO 19450:2024 po stranách z PDF: iso/s001.txt, s002.txt, ... (pro katalog/pokryti.py a pravidlo.py).

Strany před obsahem (obálka, copyright) se vynechají, takže s001.txt je první strana obsahu a článek 1 je na
s007.txt (FIRST_PAGE v pokryti.py). Text se vytahuje knihovnou pypdf (pip install pypdf), režim layout.

Použití: python katalog/iso_text.py <norma.pdf> [--cil iso]
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENTS = re.compile(r"^\s*Contents\s+(?:Page\s*)?$", re.M)    # nadpis obsahu, v layoutu se slovem Page
# pypdf slepí číslo článku s nadpisem ("1Scope", "AnnexA") a mezery v nadpisech píše jako tabulátory;
# pokryti.py nadpisy článků a příloh hledá podle mezery
FIXES = [(re.compile(r"\t+"), " "),
         (re.compile(r"^(\s*\d+(?:\.\d+)*)(?=[A-Z][a-z])", re.M), r"\1 "),
         (re.compile(r"^(\s*Annex)(?=[A-D]\b)", re.M), r"\1 ")]


def clean(text: str) -> str:
    for rx, rep in FIXES:
        text = rx.sub(rep, text)
    return text


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--cil")]
    if len(args) != 1:
        print(__doc__)
        return 2
    try:
        import pypdf
    except ImportError:
        print("Chybí knihovna pypdf: pip install pypdf")
        return 2
    logging.getLogger("pypdf").setLevel(logging.ERROR)     # "Rotated text discovered" u otočených tabulek
    target = Path(argv[argv.index("--cil") + 1]) if "--cil" in argv else ROOT / "iso"
    pages = [clean(p.extract_text(extraction_mode="layout")) for p in pypdf.PdfReader(args[0]).pages]
    start = next((i for i, t in enumerate(pages) if CONTENTS.search(t)), None)
    if start is None:
        print("V PDF není strana s obsahem (Contents): je to ISO 19450:2024?")
        return 1
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("s*.txt"):
        old.unlink()
    for n, text in enumerate(pages[start:], 1):
        (target / f"s{n:03d}.txt").write_text(text, encoding="utf-8")
    skipped = f", vynechány strany PDF 1-{start} před obsahem" if start else ""
    print(f"Zapsáno {len(pages) - start} stran do {target}{skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
