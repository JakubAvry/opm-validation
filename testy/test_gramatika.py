"""Grammar test: every sentence of testy/vety_platne.txt is read as the expected kind and dialect.

Usage (in the repository root): python testy/test_gramatika.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opmvalid import parser  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    total = 0
    for line in (ROOT / "testy" / "vety_platne.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        expected, expected_kind, _rules, text = line.split("\t", 3)
        total += 1
        s = parser.parse_sentence(text.strip().rstrip("."))
        got = "unreadable" if not s.ok else (sorted(s.dialect)[0] if s.dialect else "iso")
        kind = None
        if s.ok:
            node = s.tree.children[0]
            if node.data == "path_sentence":
                node = [c for c in node.children if hasattr(c, "data")][0]
            kind = node.data
        if got != expected or kind != expected_kind:
            bad += 1
            detail = kind if s.ok else f"col {s.error['column']} expected {s.error['expected'][:5]}"
            print(f"  {expected:8} {expected_kind:20} got {got:10} {detail}: {text}")
    print(f"vět {total}, v pořádku {total - bad}, chyb {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
