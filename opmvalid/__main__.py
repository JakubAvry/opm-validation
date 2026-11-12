"""Command line: python -m opmvalid <model.opl> [--profile iso|opcloud] [--format text|json] [--humans "A, B"]
[--primary-essence physical|informatical] [--turtle model.ttl]

Exit code (like a compiler): 0 = no errors, 1 = errors found, 2 = input could not be read.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import parser, rdf, validate


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="opmvalid")
    ap.add_argument("model")
    ap.add_argument("--profile", choices=["iso", "opcloud"], default="iso")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--humans", default=None, help="comma-separated names of the objects that are humans")
    ap.add_argument("--primary-essence", choices=["physical", "informatical"], default=None)
    ap.add_argument("--turtle", default=None, help="write the model in RDF (Turtle)")
    a = ap.parse_args(argv)
    try:
        text = Path(a.model).read_text(encoding="utf-8")
    except OSError as e:
        print(f"cannot read {a.model}: {e}", file=sys.stderr)
        return 2
    humans = {h.strip() for h in a.humans.split(",")} if a.humans is not None else None
    report = validate.check(text, a.profile, humans, a.primary_essence)
    if a.turtle:
        rdf.convert(parser.read(text), humans, a.primary_essence).serialize(destination=a.turtle, format="turtle")
    if a.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(validate.format_text(report))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
