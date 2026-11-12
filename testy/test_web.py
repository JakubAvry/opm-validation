"""Test of the web page (opmvalid/web.py): the page and the API give the same as the command line.

Starts the server on a free port of 127.0.0.1 and checks: the validation text on the page is the text output of
the command line, the RDF on the page is the graph of --turtle, every experiment of testy/pokusy.py gives its
expected codes on the page and in the API (equal to validate.check), the API equals --format json (JSON and form
input), wrong input (400, 405, 404), the OPCloud profile, non-ASCII text and CRLF line endings, an empty field.

Usage (in the repository root): python testy/test_web.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path
from urllib.parse import urlencode

import rdflib
from rdflib.compare import isomorphic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "testy"))
sys.stdout.reconfigure(encoding="utf-8")
from opmvalid import validate, web  # noqa: E402
from pokusy import CHYBNY, pokusy  # noqa: E402

srv = web.server(0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
ok = bad = 0


def expect(name, cond, info=""):
    global ok, bad
    ok += bool(cond)
    bad += not cond
    print(f"{'ok ' if cond else 'BAD'} {name}{'' if cond else '  ' + str(info)[:600]}")


def call(path, data=None, ctype=None):
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": ctype} if ctype else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode("utf-8"), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), e.headers.get("Content-Type", "")


def api(obj):
    return call("/api/check", json.dumps(obj).encode("utf-8"), "application/json")


def form(path, fields):
    return call(path, urlencode(fields).encode("utf-8"), "application/x-www-form-urlencoded")


def blocks(page):
    """(validation text, Turtle) from the two <pre> blocks of the page."""
    pres = [unescape(p) for p in re.findall(r"<pre>(.*?)</pre>", page, re.S)]
    return (pres + [None, None])[:2]


def cli(*args):
    return subprocess.run([sys.executable, "-m", "opmvalid", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8")


# the page without and with text
st, page, _ = call("/")
expect("empty page: field, no result", st == 200 and "<textarea" in page and "<pre>" not in page, st)
st, page, _ = form("/", {"text": "  \n", "profile": "iso", "humans": ""})
expect("empty field: no result", st == 200 and "<pre>" not in page, st)

text = CHYBNY.read_text(encoding="utf-8")
st, page, _ = form("/", {"text": text, "profile": "iso", "humans": "Operator"})
report_text, turtle = blocks(page)
r = cli(str(CHYBNY), "--humans", "Operator")
expect("validation on the page = text of the command line", report_text == r.stdout.rstrip("\n"),
       (report_text or "")[-300:])
ttl = Path(tempfile.mkdtemp()) / "model.ttl"
cli(str(CHYBNY), "--humans", "Operator", "--turtle", str(ttl))
try:
    g_page = rdflib.Graph().parse(data=turtle, format="turtle")
    g_cli = rdflib.Graph().parse(ttl, format="turtle")
    expect(f"RDF on the page = --turtle of the command line ({len(g_page)} triples)", isomorphic(g_page, g_cli),
           (len(g_page), len(g_cli)))
except Exception as e:  # noqa: BLE001
    expect("RDF on the page readable", False, repr(e))
st, page, _ = form("/", {"text": text, "profile": "opcloud", "humans": "Operator"})
expect("OPCloud profile on the page", "(profile opcloud)" in (blocks(page)[0] or "")
       and 'value="opcloud" selected' in page, blocks(page)[0])

# every experiment: page and API
for p in pokusy():
    st, page, _ = form("/", {"text": p["text"], "profile": p["profile"], "humans": p["humans"]})
    got = sorted({f"{m.group(1)} {m.group(2)}" for m in re.finditer(r"^([EW]) (\S+)", blocks(page)[0] or "", re.M)})
    expect(f"experiment {p['n']} on the page: {', '.join(p['ceka'])}", got == p["ceka"], got)
    humans = [h.strip() for h in p["humans"].split(",")]
    st, body, ctype = api({"text": p["text"], "profile": p["profile"], "humans": humans})
    expect(f"experiment {p['n']} in the API = validate.check",
           st == 200 and ctype.startswith("application/json")
           and json.loads(body) == validate.check(p["text"], p["profile"], set(humans)), (st, body[:300]))

# API = command line --format json
r = cli(str(CHYBNY), "--format", "json", "--humans", "Operator")
st, body, _ = api({"text": text, "profile": "iso", "humans": ["Operator"]})
expect("API = command line --format json", json.loads(body) == json.loads(r.stdout), body[:300])
st, body2, _ = form("/api/check", {"text": text, "profile": "iso", "humans": "Operator"})
expect("API with form fields = API with JSON", json.loads(body2) == json.loads(body), body2[:300])

# wrong input
st, body, _ = api({"profile": "iso"})
expect("API without text: 400", st == 400 and "text" in json.loads(body)["error"], (st, body))
st, body, _ = api({"text": "Brewing yields Tea.", "profile": "xyz"})
expect("API with a wrong profile: 400", st == 400, (st, body))
st, body, _ = call("/api/check", b"{not json", "application/json")
expect("API with broken JSON: 400", st == 400, (st, body))
st, body, _ = call("/api/check", b"[1, 2]", "application/json")
expect("API with a JSON list: 400", st == 400, (st, body))
st, body, _ = call("/api/check")
expect("API with GET: 405 and usage", st == 405 and "usage" in json.loads(body), (st, body))
st, body, _ = call("/nothing")
expect("unknown page: 404", st == 404, st)

# non-ASCII text, CRLF line endings from the browser
st, page, _ = form("/", {"text": "Kávovar can be cold or hot.\r\nBrewing changes Kávovar from cold to warm.\r\n",
                         "profile": "iso", "humans": ""})
report_text, turtle = blocks(page)
expect("non-ASCII and CRLF: name kept, finding on line 2",
       "Kávovar" in (report_text or "") and re.search(r"^E OPM-9-036\s+line 2", report_text or "", re.M)
       and "Kávovar" in (turtle or ""), report_text)

srv.shutdown()
print(f"\nTOTAL ok {ok}, bad {bad}")
sys.exit(1 if bad else 0)
