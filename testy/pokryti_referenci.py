"""How much of the OPCloud reference models (testy/reference, verified in OPCloud) the grammar reads.

Usage (in the repository root): python testy/pokryti_referenci.py [--chyby]
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opmvalid import parser  # noqa: E402

REFERENCE = ROOT / "testy" / "reference"   # OPCloud reference models


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    kinds, dialects, errors, total = Counter(), Counter(), [], 0
    t0 = time.perf_counter()
    for f in sorted(REFERENCE.glob("case*.opl")):
        doc = parser.read(f.read_text(encoding="utf-8"))
        for s in doc.sentences:
            total += 1
            if s.ok:
                node = s.tree.children[0]
                kinds[str(node.data)] += 1
                for d in s.dialect:
                    dialects[d] += 1
            else:
                errors.append((f.name, s))
    dt = time.perf_counter() - t0
    print(f"vět {total}, přečteno {total - len(errors)} ({100 * (total - len(errors)) / total:.1f} %), "
          f"nečitelných {len(errors)}, čas {dt:.1f} s ({1000 * dt / total:.0f} ms na větu)")
    print("dialekt:", dict(dialects))
    print("druhy vět:", ", ".join(f"{k} {n}" for k, n in kinds.most_common()))
    if "--chyby" in argv:
        for name, s in errors:
            print(f"  {name}:{s.line} col {s.error['column']}: {s.text[:100]}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
