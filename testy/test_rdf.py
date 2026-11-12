"""Converter smoke test: every reference model and every valid test sentence converts to RDF without an error.

A valid sentence must be readable (the 4th column of testy/vety_platne.txt is the sentence), must convert without
an exception and must give at least one triple of its own besides the sentence node. The converter catches an
exception per sentence and marks the sentence opm:unconverted (validate.py reports it as INTERNAL); here every such
mark is an error.

Ontology: every opm: class and property that the converter writes for the references, the valid
sentences and the models of testy/vety_neplatne.txt, and every one the shapes of opmvalid/tvary.ttl use, is
declared in opmvalid/ontologie.ttl.

Usage (in the repository root): python testy/test_rdf.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "testy"))
from rdflib import RDF, Graph, URIRef  # noqa: E402

from opmvalid import config, parser, rdf  # noqa: E402

REFERENCE = ROOT / "testy" / "reference"   # OPCloud reference models


def unconverted(g) -> list:
    return [f"{g.value(s, rdf.OPM.text)} ({why})" for s, why in g.subject_objects(rdf.OPM.unconverted)]


def terms(g) -> set:
    """The opm: classes and properties a graph uses (predicates, and the objects of rdf:type)."""
    found = {p for p in g.predicates() if str(p).startswith(config.OPM_NS)}
    found |= {o for o in g.objects(None, RDF.type) if str(o).startswith(config.OPM_NS)}
    return found


def undeclared(used: set) -> int:
    onto = Graph().parse(config.ONTOLOGY)
    declared = {s for s in onto.subjects(RDF.type, None) if str(s).startswith(config.OPM_NS)}
    shapes = Graph().parse(config.HERE / "tvary.ttl")
    used = used | {t for triple in shapes for t in triple
                   if isinstance(t, URIRef) and str(t).startswith(config.OPM_NS)}
    missing = sorted(str(t)[len(config.OPM_NS):] for t in used - declared)
    if missing:
        print(f"CHYBA v ontologii nejsou deklarované: {', '.join(missing)}")
    print(f"ontologie: použitých tříd a vlastností {len(used)}, nedeklarovaných {len(missing)}")
    return 1 if missing else 0


def internal() -> int:
    """A defect of the converter is reported (E INTERNAL with the line), not raised: the sentence that fails is
    marked and the other sentences are still converted and checked."""
    from opmvalid import validate
    original = rdf.Builder.invocation

    def broken(self, t):
        raise ValueError("test")

    rdf.Builder.invocation = broken
    try:
        report = validate.check("Brewing consumes Leaf.\nHeating yields Steam.\nBrewing invokes Serving.\n")
    except Exception as e:  # noqa: BLE001
        print(f"CHYBA výjimka převodu není zachycená: {type(e).__name__}: {e}")
        return 1
    finally:
        rdf.Builder.invocation = original
    got = {(e["code"], e["line"]) for e in report["entries"] if e["severity"] == "E"}
    # Heating is still checked; the missing output of Brewing is not reported (it may be in the failed sentence)
    if got != {("INTERNAL", 3), ("OPM-8-008", 2)}:
        print(f"CHYBA INTERNAL: čekáno INTERNAL na řádce 3 a OPM-8-008 na řádce 2, vyšlo {sorted(got)}")
        return 1
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    used = set()
    for f in sorted(REFERENCE.glob("case*.opl")):
        doc = parser.read(f.read_text(encoding="utf-8"))
        try:
            g = rdf.convert(doc)
            used |= terms(g)
            failed = unconverted(g)
            bad += len(failed)
            print(f"{f.name}: trojic {len(g)}" + "".join(f"\n  CHYBA {x}" for x in failed))
        except Exception:  # noqa: BLE001
            bad += 1
            print(f"{f.name}: CHYBA")
            traceback.print_exc(limit=3)
    total = 0
    base = set(rdf.convert(parser.read("")))                   # triples of a model without sentences
    for line in (ROOT / "testy" / "vety_platne.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        text = line.split("\t", 3)[3].strip()
        total += 1
        try:
            doc = parser.read(text)
            if len(doc.sentences) != 1 or not doc.sentences[0].ok:
                bad += 1
                print(f"CHYBA nečitelná věta | {text}")
                continue
            g = rdf.convert(doc)
            used |= terms(g)
            failed = unconverted(g)
            own = [t for t in g if t not in base and t[0] != rdf.M["s1_1"]]        # besides the sentence node
            if failed:
                bad += 1
                print(f"CHYBA {failed[0]}")
            elif not own:
                bad += 1
                print(f"CHYBA věta nedala žádnou trojici | {text}")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"CHYBA {type(e).__name__}: {e} | {text}")
    from test_neplatne import cases
    for c in cases((ROOT / "testy" / "vety_neplatne.txt").read_text(encoding="utf-8"), {}):
        humans = set(c["opts"]["humans"].split(",")) if "humans" in c["opts"] else None
        used |= terms(rdf.convert(parser.read("\n".join(c["lines"])), humans))
    bad += undeclared(used)
    bad += internal()
    print(f"platných vět {total}, chyb {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
