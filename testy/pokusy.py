"""Hand experiments as data (from the testing guide): one edit of testy/ukazka_platny.opl each, with the
E and W codes it must give. testy/test_web.py runs every one through the web page and the API.

The base model and most edits are those of testy/test_nova_domena.py (expectations written before its first run).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATNY = ROOT / "testy" / "ukazka_platny.opl"
CHYBNY = ROOT / "testy" / "ukazka_chybny.opl"


def _line(n, new):
    def f(ls):
        ls = list(ls)
        ls[n - 1] = new
        return ls
    return f


def _after(n, *new):
    return lambda ls: ls[:n] + list(new) + ls[n:]


def _end(*new):
    return lambda ls: ls + list(new)


def _everywhere(old, new):
    return lambda ls: [x.replace(old, new) for x in ls]


def _chain(*fs):
    def f(ls):
        for g in fs:
            ls = g(ls)
        return ls
    return f


def _same(ls):
    return ls


H = "Librarian"
# (description of the edit, edit, humans, profile, expected E/W codes)
_POKUSY = [
    ("ř. 3: lent → reserved", _line(3, "Book can be available or reserved."), H, "iso", {"E OPM-9-036"}),
    ("beze změny, lidé „Clerk“", _same, "Clerk", "iso", {"E OPM-9-028"}),
    ("beze změny, lidé „Librarian, Catalog System“", _same, "Librarian, Catalog System", "iso", {"E OPM-9-032"}),
    ("za ř. 13 přidat „Checking affects Book.“", _after(13, "Checking affects Book."), H, "iso", {"E OPM-8-004"}),
    ("ř. 10: přidat podproces Greeting, na konec „Librarian handles Greeting.“",
     _chain(_end("Librarian handles Greeting."),
            _line(10, "Lending zooms into Checking, Recording, and Greeting, in that sequence.")),
     H, "iso", {"E OPM-8-002"}),
    ("za ř. 7 druhý systémový proces Returning",
     _after(7, "Librarian handles Returning.", "Returning changes Book from lent to available."),
     H, "iso", {"E OPM-14-068"}),
    ("na konec „Lending consumes Request Form.“", _end("Lending consumes Request Form."), H, "iso",
     {"E OPM-14-051"}),
    ("ř. 15: pravděpodobnosti 0,6 a 0,3",
     _line(15, "Recording yields Loan Record with probability 0,6 or Error Note with probability 0,3."),
     H, "iso", {"E OPM-12-023"}),
    ("za ř. 3 dva výchozí stavy",
     _after(3, "State available of Book is default.", "State lent of Book is default."), H, "iso",
     {"E OPM-7-023"}),
    ("všude Recording → Record Creation", _everywhere("Recording", "Record Creation"), H, "iso", {"W OPM-B-007"}),
    ("stav lent → lending (ř. 3, 6, 13)",
     _chain(_line(3, "Book can be available or lending."), _everywhere("to lent.", "to lending.")),
     H, "iso", {"W OPM-B-008"}),
    ("ř. 4: librarian malým písmenem", _line(4, "librarian handles Lending."), H, "iso", {"E OPM-7-004"}),
    ("na konec „Checking consumes“", _end("Checking consumes"), H, "iso", {"E OPM-9-006"}),
    ("na konec „Book Lending quickly.“", _end("Book Lending quickly."), H, "iso", {"E SYNTAX"}),
    ("smazat ř. 1 (titulek)", lambda ls: ls[1:], H, "iso", {"W OPM-14-078"}),
    ("na konec „Recording yields Checking.“", _end("Recording yields Checking."), H, "iso",
     {"E OPM-7-009"}),                      # and I OPM-12-002: two "yields" sentences (I in the ISO profile, N-062)
]


def pokusy() -> list[dict]:
    base = PLATNY.read_text(encoding="utf-8").splitlines()
    return [{"n": n, "uprava": desc, "text": "\n".join(edit(base)) + "\n", "humans": humans, "profile": profile,
             "ceka": sorted(want)}
            for n, (desc, edit, humans, profile, want) in enumerate(_POKUSY, 1)]
