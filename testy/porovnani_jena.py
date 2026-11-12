"""Two engines, one result: the shapes of opmvalid/tvary.ttl run in pySHACL and in Apache Jena SHACL on the same RDF.

Models: the OPCloud references (testy/reference, profiles opcloud and iso) and every case of
testy/vety_neplatne.txt (its own profile). For each model both engines get the same data (converter output plus
the ontology) and the same shapes (filtered by profile); their results are compared as sets of
(focus node, value, severity, constraint component, shape), the shape as its IRI or "bnode" for a nested one.
Messages are not compared (the report text comes from the catalogue).

Jena: nastroje/ (portable Java 17 + Jena 5.2.0, README, Prostředí). Jena reads file paths as IRIs, so the files are
written to a temporary directory without spaces and passed as relative paths.

Usage (in the repository root): python testy/porovnani_jena.py [--jen-pripady]
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pyshacl import validate as shacl_validate
from rdflib import RDF, BNode, Graph, Literal, Namespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "testy"))
from opmvalid import parser, rdf, validate  # noqa: E402
from test_neplatne import cases  # noqa: E402

SH = Namespace("http://www.w3.org/ns/shacl#")
JAVA = ROOT / "nastroje" / "jdk-17.0.21+9-jre" / "bin" / "java.exe"
JENA_LIB = (ROOT / "nastroje" / "apache-jena-5.2.0" / "lib").as_posix() + "/*"
REFERENCE = ROOT / "testy" / "reference"   # OPCloud reference models


def key_set(report: Graph) -> set:
    out = set()
    for r in report.subjects(RDF.type, SH.ValidationResult):
        value = report.value(r, SH.value)
        if isinstance(value, Literal) and value.value is not None and not isinstance(value.value, str):
            value = repr(float(value.value)) if isinstance(value.value, (int, float)) or hasattr(value.value, "as_tuple") \
                else str(value.value)
        shape = report.value(r, SH.sourceShape)
        out.add((str(report.value(r, SH.focusNode)), None if isinstance(value, BNode) else str(value),
                 str(report.value(r, SH.resultSeverity)), str(report.value(r, SH.sourceConstraintComponent)),
                 "bnode" if isinstance(shape, BNode) else str(shape)))
    return out


def jena(tmp: Path, shapes_file: str, data_file: str) -> Graph:
    run = subprocess.run([str(JAVA), "-cp", JENA_LIB, "shacl.shacl", "validate", "--shapes", shapes_file,
                          "--data", data_file], cwd=tmp, capture_output=True, text=True, encoding="utf-8")
    if run.returncode not in (0, 1) or not run.stdout.strip():
        raise RuntimeError(run.stderr[-2000:])
    return Graph().parse(data=run.stdout, format="turtle")


def models(only_cases: bool) -> list:
    out = []
    if not only_cases:
        for profile in ("opcloud", "iso"):
            for f in sorted(REFERENCE.glob("case*.opl")):
                out.append((f"reference {f.stem} ({profile})", f.read_text(encoding="utf-8"), profile, None))
    explained = {}
    for n, c in enumerate(cases((ROOT / "testy" / "vety_neplatne.txt").read_text(encoding="utf-8"), explained), 1):
        humans = set(c["opts"]["humans"].split(",")) if "humans" in c["opts"] else None
        label = f"case {n:2} {', '.join(sorted(c['codes'])) or '-'}"
        out.append((label, "\n".join(c["lines"]), c["opts"].get("profile", "iso"), humans))
    return out


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not JAVA.exists() or not (ROOT / "nastroje" / "apache-jena-5.2.0" / "lib").is_dir():
        print("Apache Jena není ve složce nastroje/ (Java 17 + Jena 5.2.0, návod v README): srovnání se přeskakuje.")
        return 0
    t0 = time.perf_counter()
    bad = total = 0
    with tempfile.TemporaryDirectory(prefix="opmjena_") as d:
        tmp = Path(d)
        for profile in ("iso", "opcloud"):
            validate.shapes(profile)[0].serialize(tmp / f"shapes_{profile}.ttl", format="turtle")
        for n, (label, text, profile, humans) in enumerate(models("--jen-pripady" in argv)):
            shape_graph, onto, _ = validate.shapes(profile)
            g = rdf.convert(parser.read(text), humans)
            _, py_report, _ = shacl_validate(g, shacl_graph=shape_graph, ont_graph=onto, inference="none",
                                             allow_warnings=True)
            data = Graph()
            for t in g:
                data.add(t)
            for t in onto:
                data.add(t)
            data.serialize(tmp / f"data_{n}.ttl", format="turtle")
            jena_report = jena(tmp, f"shapes_{profile}.ttl", f"data_{n}.ttl")
            a, b = key_set(py_report), key_set(jena_report)
            total += len(a)
            same = a == b
            bad += not same
            print(f"{'ok ' if same else 'BAD'} {label}: pySHACL {len(a)}, Jena {len(b)}")
            for k in sorted(a - b):
                print("      only pySHACL:", k)
            for k in sorted(b - a):
                print("      only Jena:   ", k)
    print(f"models {n + 1}, same {n + 1 - bad}, different {bad}; results compared {total}; "
          f"{time.perf_counter() - t0:.0f} s")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
