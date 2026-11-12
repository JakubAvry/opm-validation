"""One rule from end to end, for a spot check "does the SHACL rule match the norm": the catalogue record, the
"shall"/"should" sentences of its ISO clause, the SHACL shape and the tests that show it.

The ISO text is read from the folder iso/ (not in the repository, see katalog/pokryti.py) and only shown locally. info() returns the
data (used by the web page, opmvalid/web.py); main() prints it.

Usage (in the repository root):
  python katalog/pravidlo.py OPM-9-036      (or just 9-036)
  python katalog/pravidlo.py 9-036 --run    also run the invalid test cases of the rule through the validator
  python katalog/pravidlo.py --random       a random checked rule (SHACL or grammar), for a spot check
"""
from __future__ import annotations

import random
import re
import sys
from functools import lru_cache
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (HERE, ROOT, ROOT / "testy"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
from pokryti import ANNEX, CATEGORY, FIRST_PAGE, HEADING, ISO_PAGES, WORD, _accept  # noqa: E402

ID = re.compile(r"OPM-[0-9A-Z]+-\d+|OPC-\d+")


@lru_cache(maxsize=1)
def rules() -> dict:
    cat = yaml.safe_load((HERE / "pravidla.yaml").read_text(encoding="utf-8"))
    return {r["id"]: r for r in cat["rules"]}


@lru_cache(maxsize=1)
def iso_clauses() -> dict:
    """Text of every clause (without its subclauses) and the page files it is on, read once."""
    out: dict[str, tuple[list, list]] = {}
    cur = None
    for f in sorted(ISO_PAGES.glob("s*.txt")):
        if int(f.stem[1:]) < FIRST_PAGE:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            m, a = HEADING.match(line), ANNEX.match(line)
            if a:
                cur = a.group(1)
            elif m and _accept(m.group(1), cur):
                cur = m.group(1)
            if cur is not None:
                lines, files = out.setdefault(cur, ([], []))
                lines.append(line.strip())
                if f.name not in files:
                    files.append(f.name)
    return out


def normalize(code: str) -> str:
    code = code.strip().upper()
    return code if code.startswith(("OPM-", "OPC-")) else "OPM-" + code


def random_code() -> str:
    return random.choice([i for i, r in rules().items() if r["check"]["by"] in ("shacl", "grammar")])


def info(code: str, run: bool = False, limit: int | None = None) -> dict | None:
    """Everything about one rule; None when the code is not in the catalogue. run: validate the invalid cases
    (the first `limit` of them)."""
    code = normalize(code)
    r = rules().get(code)
    if r is None:
        return None
    chk = r["check"]
    out = {
        "id": code,
        "where": (f"ISO {r['iso']}, printed page {r['page']} (PDF page {r['page'] + 8})" if r.get("page")
                  else f"ISO {r['iso']}" if str(r["iso"]) != "-" else "no ISO clause"),
        "iso": str(r["iso"]), "strength": r["strength"], "source": r["source"],
        "category": r["category"], "category_name": CATEGORY[r["category"]],
        "by": chk["by"], "core": chk.get("core"), "status": chk.get("status"), "needs": chk.get("needs"),
        "rule": r["rule"], "plan": chk.get("plan"), "production": chk.get("production"), "reason": chk.get("reason"),
        "severity": r.get("severity"), "message": r.get("message"), "hint": r.get("hint"), "note": r.get("note"),
        "iso_dir": str(ISO_PAGES), "iso_files": [], "iso_sentences": None,
        "shared": [], "shapes": [], "valid": [], "explained": None, "cases": [],
    }
    # ISO text: the sentences with shall / should of the clause
    if out["iso"] != "-" and ISO_PAGES.exists():
        lines, files = iso_clauses().get(out["iso"], ([], []))
        text = " ".join(lines)
        out["iso_files"] = files
        out["iso_sentences"] = [s for s in re.split(r"(?<=[.;])\s+(?=[A-Z])", text)
                                if any(rx.search(s) for rx in WORD.values())]
    # SHACL shape: own, or the shared shapes named in the plan
    if chk["by"] == "shacl":
        if r.get("shape"):
            out["shapes"] = [r["shape"].rstrip()]
        else:
            out["shared"] = [i for i in ID.findall(str(chk.get("plan", ""))) if rules().get(i, {}).get("shape")]
            out["shapes"] = [rules()[i]["shape"].rstrip() for i in out["shared"]]
    # tests
    from test_neplatne import cases  # noqa: E402
    explained: dict = {}
    out["cases"] = [{"codes": sorted(c["codes"]), "opts": c["opts"], "lines": [x for x in c["lines"] if x.strip()],
                     "results": None}
                    for c in cases((ROOT / "testy" / "vety_neplatne.txt").read_text(encoding="utf-8"), explained)
                    if code in c["codes"]]
    out["explained"] = explained.get(code)
    for line in (ROOT / "testy" / "vety_platne.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if not line.startswith("#") and len(parts) >= 4 and code in [i.strip() for i in parts[2].split(",")]:
            out["valid"].append(parts[3])
    if run:
        from opmvalid import validate  # noqa: E402
        for c in out["cases"][:limit]:
            humans = {h.strip() for h in c["opts"]["humans"].split(",")} if "humans" in c["opts"] else None
            rep = validate.check("\n".join(c["lines"]), c["opts"].get("profile", "iso"), humans)
            c["results"] = [e for e in rep["entries"] if e["severity"] in "EW"]
    return out


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in argv if not a.startswith("--")]
    if "--random" in argv:
        args = [random_code()]
    if not args:
        print(__doc__)
        return 2
    d = info(args[0], run="--run" in argv, limit=3)
    if d is None:
        print(f"{normalize(args[0])}: not in katalog/pravidla.yaml")
        return 2

    print(f"{d['id']}  {d['where']}, {d['strength']}, source {d['source']}")
    print(f"category {d['category']} {d['category_name']}; checked by {d['by']}"
          + (f" ({'SHACL Core' if d['core'] else 'SHACL-SPARQL'})" if d["by"] == "shacl" else "")
          + (f"; status {d['status']}" if d["status"] else "")
          + (f"; needs {d['needs']}" if d["needs"] else ""))
    print(f"\nRULE (paraphrase): {d['rule']}")
    for key in ("plan", "production", "reason"):
        if d[key]:
            print(f"{key.upper()}: {d[key]}")
    if d["severity"]:
        print(f"\nREPORT {d['severity']}: {d['message'] or ''}")
        if d["hint"]:
            print(f"FIX: {d['hint']}")
    if d["note"]:
        print(f"NOTE: {d['note']}")

    if d["iso_sentences"] is not None:
        print(f"\nISO {d['iso']} ({', '.join(d['iso_files']) or 'not found'} in {d['iso_dir']}):")
        for s in d["iso_sentences"] or ["(no sentence with shall / should; the requirement is stated without them)"]:
            print(f"  - {s}")
    elif d["iso"] != "-":
        print(f"\n(ISO text not found: {d['iso_dir']})")

    if d["by"] == "shacl":
        print("\nSHACL SHAPE" + (f" (shared: {', '.join(d['shared'])})" if d["shared"] else "") + ":")
        for s in d["shapes"]:
            print("  " + s.replace("\n", "\n  "))

    print(f"\nTESTS: {len(d['cases'])} invalid case(s) in testy/vety_neplatne.txt, "
          f"{len(d['valid'])} valid sentence(s) in testy/vety_platne.txt")
    if d["explained"]:
        print(f"  no own case: {d['explained']}")
    for s in d["valid"][:5]:
        print(f"  valid: {s}")
    for c in d["cases"][:3]:
        opts = "".join(f" | {k}={v}" for k, v in c["opts"].items())
        print(f"  == {', '.join(c['codes'])}{opts}")
        for line in c["lines"]:
            print(f"     {line}")
        for e in c["results"] or []:
            print(f"     -> {e['severity']} {e['code']} line {e['line']}: {e['message']}")
    if len(d["cases"]) > 3:
        print(f"  … and {len(d['cases']) - 3} more")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
