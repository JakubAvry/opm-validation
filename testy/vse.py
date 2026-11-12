"""All tests in one run: each script with its exit code, result line and time (about 10 min; with --jena 22 min).

Usage (in the repository root): python testy/vse.py [--jena]
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    ("testy/test_gramatika.py", "valid sentences: dialect and kind of every sentence"),
    ("testy/test_neplatne.py", "invalid models: exactly the expected codes, messages filled"),
    ("testy/test_nova_domena.py", "new domain (book lending) and the command line"),
    ("testy/test_web.py", "web page and API give the same as the library and the command line"),
    ("testy/test_rdf.py", "OPL -> RDF of all references and valid sentences"),
    ("testy/pokryti_referenci.py", "every sentence of the OPCloud references readable"),
    ("testy/validace_referenci.py", "validator on the references (no unfilled message)"),
    ("katalog/tvary.py --check", "shapes generated from the catalogue, statuses agree"),
    ("katalog/pokryti.py", "every shall / should of the norm has a catalogue record"),
]


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    tests = TESTS + ([("testy/porovnani_jena.py", "pySHACL and Apache Jena give the same results")]
                     if "--jena" in argv else [])
    failed = 0
    for cmd, what in tests:
        t = time.time()
        r = subprocess.run([sys.executable, *cmd.split()], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        lines = [x for x in (r.stdout + r.stderr).splitlines() if x.strip()]
        ok = r.returncode == 0
        failed += not ok
        print(f"{'ok ' if ok else 'BAD'} {cmd:<30} {time.time() - t:5.0f} s  {what}")
        # the result line: the last one, or for the reference reading the line with the totals
        result = next((x for x in lines if x.startswith("vět ") and "přečteno" in x), lines[-1] if lines else None)
        print(f"      {result or '(no output)'}")
        if not ok:
            for x in lines[-15:-1]:
                print(f"      {x}")
    print(f"\n{len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
