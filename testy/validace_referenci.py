"""Validator on the OPCloud references (testy/reference): counts of entries per code and profile.

The references are verified in OPCloud, so in the OPCloud profile every error is either a real violation of
ISO 19450 that OPCloud allows, or a false positive of the validator; each is to be looked at.

Usage (in the repository root): python testy/validace_referenci.py [--profile iso|opcloud] [--vypis]
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opmvalid import validate  # noqa: E402

REFERENCE = ROOT / "testy" / "reference"   # OPCloud reference models


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    profile = argv[argv.index("--profile") + 1] if "--profile" in argv else "opcloud"
    total = Counter()
    unfilled = []
    t0 = time.perf_counter()
    for f in sorted(REFERENCE.glob("case*.opl")):
        report = validate.check(f.read_text(encoding="utf-8"), profile)
        unfilled += [(f.stem, e) for e in report["entries"] if "…" in e["message"] or "…" in (e["hint"] or "")]
        per = Counter((e["severity"], e["code"]) for e in report["entries"] if e["code"] != "NOT-CHECKED")
        total += per
        errors = {c: n for (s, c), n in per.items() if s == "E"}
        print(f"{f.stem}: chyb {report['errors']}, varování {report['warnings']} {errors or ''}")
        if "--vypis" in argv:
            for e in report["entries"]:
                if e["severity"] == "E":
                    print(f"    {e['code']} ř. {e['line']}: {e['message'][:160]}")
    print(f"\nprofil {profile}, čas {time.perf_counter() - t0:.0f} s")
    for (s, c), n in sorted(total.items()):
        print(f"  {s} {c}: {n}")
    print(f"hlášení s nevyplněným údajem („…“): {len(unfilled)}")
    for stem, e in unfilled:
        print(f"    {stem} {e['code']} ř. {e['line']}: {e['message'][:160]}")
    return 1 if unfilled else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
