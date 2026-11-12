"""Test on a new domain (book lending), written after the test sets and not taken from them: one valid model,
one mutation at a time with the expected codes written down before the first run, and the command line (exit
codes, JSON, Turtle, files with a byte order mark or CRLF line endings, an empty file).

First run: 23 of 28 as expected. Found: a file with a BOM was unreadable, OPM-8-002 missed a
subprocess that transforms nothing, OPM-8-004 missed a change and an effect of one object (fixed); one
expectation was wrong (OPM-12-002, see the case).

Usage (in the repository root): python testy/test_nova_domena.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
from opmvalid import validate  # noqa: E402

BASE = """OPL specification of Book Lending
SD: Book Lending
Book can be available or lent.
Librarian handles Lending.
Lending requires Catalog System.
Lending changes Book from available to lent.
Lending yields Loan Record.
SD1: Lending in-zoomed
SD is refined by in-zooming Lending in SD1.
Lending zooms into Checking and Recording, in that sequence.
Librarian handles Checking.
Checking requires Catalog System.
Checking changes Book from available to lent.
Recording requires Catalog System.
Recording yields Loan Record."""

HUMANS = {"Librarian"}


def replace(old, new):
    assert BASE.count(old) == 1, old
    return BASE.replace(old, new)


def add_after(line, extra):
    assert BASE.count(line) == 1, line
    return BASE.replace(line, line + "\n" + extra)


# (name, model, expected E+W codes, humans, profile)
CASES = [
    ("valid model", BASE, set(), HUMANS, "iso"),
    ("valid model, OPCloud profile", BASE, set(), HUMANS, "opcloud"),
    ("undeclared state", replace("available or lent.", "available or reserved."), {"OPM-9-036"}, HUMANS, "iso"),
    ("agent not human", BASE, {"OPM-9-028"}, {"Clerk"}, "iso"),
    ("instrument is human", BASE, {"OPM-9-032"}, {"Librarian", "Catalog System"}, "iso"),
    # first expectation was OPM-7-009 only; the two "Recording yields" sentences also break OPM-12-002 (AND links
    # of one process in one sentence), a correct finding. OPM-12-002 is I in the ISO profile
    # (N-062) and E only in the OPCloud profile.
    ("thing both object and process", add_after("Recording yields Loan Record.", "Recording yields Checking."),
     {"OPM-7-009"}, HUMANS, "iso"),
    ("thing both object and process, OPCloud profile",
     add_after("Recording yields Loan Record.", "Recording yields Checking."),
     {"OPM-7-009", "OPM-12-002"}, HUMANS, "opcloud"),
    ("two procedural links", add_after("Checking changes Book from available to lent.", "Checking affects Book."),
     {"OPM-8-004"}, HUMANS, "iso"),
    ("subprocess transforms nothing",
     replace("into Checking and Recording, in that sequence.",
             "into Checking, Recording, and Greeting, in that sequence.\nLibrarian handles Greeting."),
     {"OPM-8-002"}, HUMANS, "iso"),
    ("two systemic processes in SD",
     add_after("Lending yields Loan Record.", "Librarian handles Returning.\n"
               "Returning changes Book from lent to available."),
     {"OPM-14-068"}, HUMANS, "iso"),
    ("consumption at the in-zoomed process",
     add_after("Recording yields Loan Record.", "Lending consumes Request Form."),
     {"OPM-14-051"}, HUMANS, "iso"),
    ("probabilities do not add up",
     replace("Recording yields Loan Record.",
             "Recording yields Loan Record with probability 0,6 or Error Note with probability 0,3."),
     {"OPM-12-023"}, HUMANS, "iso"),
    ("two default states", add_after("Book can be available or lent.",
                                     "State available of Book is default.\nState lent of Book is default."),
     {"OPM-7-023"}, HUMANS, "iso"),
    ("process name not a gerund", BASE.replace("Recording", "Record Creation"), {"OPM-B-007"}, HUMANS, "iso"),
    ("lowercase name", replace("Librarian handles Lending.", "librarian handles Lending."),
     {"OPM-7-004"}, HUMANS, "iso"),
    ("unfinished sentence", add_after("Recording yields Loan Record.", "Checking consumes"),
     {"OPM-9-006"}, HUMANS, "iso"),
    ("no recognisable form", add_after("Recording yields Loan Record.", "Book Lending quickly."),
     {"SYNTAX"}, HUMANS, "iso"),
    ("missing title", BASE.split("\n", 1)[1], {"OPM-14-078"}, HUMANS, "iso"),
]


def codes(rep):
    return {e["code"] for e in rep["entries"] if e["severity"] in "EW"}


ok = bad = 0
for name, model, want, humans, profile in CASES:
    rep = validate.check(model, profile, humans)
    got = codes(rep)
    good = got == want
    ok += good
    bad += not good
    print(f"{'ok ' if good else 'BAD'} {name}: want {sorted(want) or '-'}, got {sorted(got) or '-'}")
    if not good or name in ("valid model",):
        for e in rep["entries"]:
            if e["severity"] in "EW":
                print(f"      {e['severity']} {e['code']} line {e['line']}: {e['message']}")
    else:
        for e in rep["entries"]:
            if e["code"] in want:
                print(f"      line {e['line']}: {e['message']}")
                break

# ---------------------------------------------------------------- CLI
print("\nCLI")
tmp = Path(tempfile.mkdtemp())


def cli(*args):
    r = subprocess.run([sys.executable, "-m", "opmvalid", *args], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8")
    return r.returncode, r.stdout, r.stderr


def expect(name, cond, info=""):
    global ok, bad
    ok += bool(cond)
    bad += not cond
    print(f"{'ok ' if cond else 'BAD'} {name}{'' if cond else '  ' + info}")


valid = tmp / "valid.opl"
valid.write_text(BASE + "\n", encoding="utf-8")
broken = tmp / "broken.opl"
broken.write_text(replace("available or lent.", "available or reserved.") + "\n", encoding="utf-8")

rc, out, err = cli(str(valid), "--humans", "Librarian")
expect("valid model: exit 0", rc == 0, f"rc={rc} {out[-300:]} {err[-300:]}")
rc, out, err = cli(str(broken), "--humans", "Librarian")
expect("model with an error: exit 1", rc == 1, f"rc={rc}")
expect("text report names line and code", "line 6" in out and "OPM-9-036" in out, out[-500:])
rc, out, err = cli(str(tmp / "missing.opl"))
expect("missing file: exit 2, no traceback", rc == 2 and "Traceback" not in err, f"rc={rc} {err[-300:]}")

rc, out, err = cli(str(broken), "--format", "json", "--humans", "Librarian")
try:
    j = json.loads(out)
    expect("JSON parses and has the error with its line",
           any(e["code"] == "OPM-9-036" and e["line"] == 6 for e in j["entries"]), out[:300])
except ValueError as e:
    expect("JSON parses", False, str(e))

ttl = tmp / "model.ttl"
rc, out, err = cli(str(valid), "--humans", "Librarian", "--turtle", str(ttl))
try:
    import rdflib
    g = rdflib.Graph().parse(ttl, format="turtle")
    expect(f"Turtle written and readable ({len(g)} triples)", len(g) > 0)
except Exception as e:  # noqa: BLE001
    expect("Turtle written and readable", False, repr(e))

# encodings and line endings from Windows editors
for label, data in [("UTF-8 with BOM", b"\xef\xbb\xbf" + (BASE + "\n").encode("utf-8")),
                    ("CRLF line endings", (BASE + "\n").replace("\n", "\r\n").encode("utf-8")),
                    ("BOM + CRLF", b"\xef\xbb\xbf" + (BASE + "\n").replace("\n", "\r\n").encode("utf-8")),
                    ("trailing spaces and blank lines", ("\n\n" + BASE.replace("\n", "  \n") + "\n\n").encode())]:
    f = tmp / "enc.opl"
    f.write_bytes(data)
    rc, out, err = cli(str(f), "--humans", "Librarian")
    expect(f"{label}: same as plain file (exit 0)", rc == 0, f"rc={rc} {out[:400]} {err[-300:]}")

empty = tmp / "empty.opl"
empty.write_text("", encoding="utf-8")
rc, out, err = cli(str(empty))
# empty input is an error
expect("empty file: error EMPTY, exit 1, no crash", rc == 1 and "EMPTY" in out and "Traceback" not in err,
       f"rc={rc} {out[-300:]} {err[-300:]}")
print(f"      empty file -> exit {rc}: {out.strip().splitlines()[-1] if out.strip() else err.strip()[-200:]}")

print(f"\nTOTAL ok {ok}, bad {bad}")
sys.exit(1 if bad else 0)
