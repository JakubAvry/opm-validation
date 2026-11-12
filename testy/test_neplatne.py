"""Test of the validator on invalid models: testy/vety_neplatne.txt.

Every case must give exactly its expected E and W codes (nothing else: no false finding, no cascade), and every
message and hint must be filled (no "…"). Every shape of opmvalid/tvary.ttl needs a case or a reason ("!!").

Usage (in the repository root): python testy/test_neplatne.py [-v]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opmvalid import validate  # noqa: E402


def cases(text: str, explained: dict) -> list:
    out = []
    for line in text.splitlines():
        if line.startswith("//"):
            continue
        if line.startswith("!! "):
            code, reason = line[3:].split(":", 1)
            explained[code.strip()] = reason.strip()
            continue
        if line.startswith("== "):
            head = [p.strip() for p in line[3:].split("|")]
            opts = dict(p.split("=", 1) for p in head[1:])
            codes = set() if head[0] == "-" else {c.strip() for c in head[0].split(",")}
            out.append({"codes": codes, "opts": opts, "lines": []})
        elif out:
            out[-1]["lines"].append(line)
    return out


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    verbose = "-v" in argv
    bad, tested, explained = 0, set(), {}
    all_cases = cases((ROOT / "testy" / "vety_neplatne.txt").read_text(encoding="utf-8"), explained)
    for c in all_cases:
        humans = set(c["opts"]["humans"].split(",")) if "humans" in c["opts"] else None
        report = validate.check("\n".join(c["lines"]), c["opts"].get("profile", "iso"), humans)
        entries = report["entries"]
        got = {e["code"] for e in entries if e["severity"] in ("E", "W")}
        unfilled = [e for e in entries if "…" in e["message"] or "…" in (e["hint"] or "")]
        # "| info=A, B": these I entries must be there too (e.g. DIALECT)
        info = {i.strip() for i in c["opts"].get("info", "").split(",") if i.strip()}
        got_info = {e["code"] for e in entries if e["severity"] == "I"}
        tested |= c["codes"]
        ok = got == c["codes"] and info <= got_info and not unfilled
        bad += not ok
        if not ok or verbose:
            print(f"{'ok ' if ok else 'BAD'} expected {sorted(c['codes']) or '-'}, got {sorted(got) or '-'}"
                  f"{', unfilled' if unfilled else ''}"
                  f"{f', info missing: {sorted(info - got_info)}' if info - got_info else ''}")
            for e in entries:
                if e["severity"] in ("E", "W") or verbose:
                    print(f"      {e['severity']} {e['code']:11} line {e['line']}: {e['message']}")
                    if e["hint"]:
                        print(f"        hint: {e['hint']}")
    shapes = set(re.findall(r'opm:rule "([^"]+)"', (ROOT / "opmvalid" / "tvary.ttl").read_text(encoding="utf-8")))
    missing = shapes - tested - set(explained)
    print(f"cases {len(all_cases)}, ok {len(all_cases) - bad}, bad {bad}; shapes {len(shapes)}: with a case "
          f"{len(shapes & tested)}, explained {len(shapes & set(explained))}, without a case: "
          f"{', '.join(sorted(missing)) or 'none'}")
    return 1 if bad or missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
