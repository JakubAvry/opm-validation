"""Local web page over the validator: one field for OPL text, and the result: the SHACL validation report (the
text of the command line) and the model in RDF (Turtle). Plain HTML, no CSS, no JavaScript, standard library only.

Same code path as the command line: validate.check() and rdf.convert(), the report text from validate.format_text()
(here without the limit of entries per code).

API (no page, for other programs): POST /api/check with JSON {"text": "...", "profile": "iso" | "opcloud",
"humans": ["A", "B"] or null, "primary_essence": null | "physical" | "informatical"}, or with the form fields
(text, profile, humans "A, B") -> the report as JSON, the same as --format json.

Runs only on 127.0.0.1.

Usage (in the repository root): python -m opmvalid.web [--port 8766] [--no-browser]
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import parser, rdf, validate

PORT = 8766
CHECK = threading.Lock()      # one validation at a time (the cached shape graphs are shared)
esc = html.escape
PLACEHOLDER = "Put OPL here.."


def humans_of(value) -> set | None:
    """Form: "A, B" (empty = not given, the agent rules are not checked). JSON: a list (empty = nobody) or null."""
    if value is None:
        return None
    if isinstance(value, list):
        return {str(h).strip() for h in value if str(h).strip()}
    value = str(value).strip()
    return {h.strip() for h in value.split(",") if h.strip()} if value else None


def run_check(text: str, profile: str, humans: set | None, essence: str | None = None) -> dict:
    with CHECK:
        return validate.check(text, profile, humans, essence or None)


def page(text: str = "", profile: str = "iso", humans: str = "") -> str:
    result = ""
    if text.strip():
        report = run_check(text, profile, humans_of(humans))
        turtle = rdf.convert(parser.read(text), humans_of(humans), None).serialize(format="turtle")
        result = (f'<h2 id="vysledek">Výsledek validace</h2>\n<pre>{esc(validate.format_text(report, limit=False))}'
                  f'</pre>\n<h2 id="rdf">Model v RDF (Turtle)</h2>\n<pre>{esc(turtle)}</pre>\n')
    options = "".join(f'<option value="{v}"{" selected" if v == profile else ""}>{label}</option>'
                      for v, label in (("iso", "ISO 19450"), ("opcloud", "OPCloud")))
    return f"""<!doctype html>
<html lang="cs"><head><meta charset="utf-8"><title>Validace OPL</title></head>
<body>
<h1>Validace OPL</h1>
<p>OPL -> RDF -> SHACL validation</p>
<form method="post" action="/" accept-charset="utf-8">
<p><textarea name="text" rows="18" cols="110" placeholder="{esc(PLACEHOLDER)}">{esc(text)}</textarea></p>
<p>Profil: <select name="profile">{options}</select>
Humans: <input name="humans" size="40" value="{esc(humans)}">
<button type="submit">Zkontrolovat</button></p>
</form>
{result}</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "opmvalid"

    def log_message(self, fmt, *args):  # quiet console
        pass

    def send(self, body: str, ctype: str = "text/html; charset=utf-8", status: int = 200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, status: int = 200):
        self.send(json.dumps(obj, ensure_ascii=False, indent=2), "application/json; charset=utf-8", status)

    def form(self, raw: bytes) -> dict:
        return {k: v[-1] for k, v in parse_qs(raw.decode("utf-8"), keep_blank_values=True).items()}

    def do_GET(self):
        self.safely(self.get)

    def do_POST(self):
        self.safely(self.post)

    def safely(self, fn):
        try:
            fn()
        except Exception:  # noqa: BLE001 - a local tool: show the traceback
            self.send("<!doctype html><meta charset=\"utf-8\"><title>Chyba</title><pre>"
                      + esc(traceback.format_exc()) + "</pre>", status=500)

    def get(self):
        path = urlparse(self.path).path
        if path == "/":
            self.send(page())
        elif path == "/api/check":
            self.send_json({"error": "use POST", "usage": __doc__.split("API (no page, for other programs): ", 1)[1]
                           .split("\n\n")[0]}, 405)
        else:
            self.send("<!doctype html><meta charset=\"utf-8\"><title>Nenalezeno</title><p>Stránka není. "
                      '<a href="/">Validace OPL</a></p>', status=404)

    def post(self):
        path = urlparse(self.path).path
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if path == "/":
            f = self.form(raw)
            profile = f.get("profile", "iso") if f.get("profile") in ("iso", "opcloud") else "iso"
            self.send(page(f.get("text", ""), profile, f.get("humans", "")))
        elif path == "/api/check":
            try:
                if self.headers.get("Content-Type", "").startswith("application/json"):
                    d = json.loads(raw.decode("utf-8"))
                    text, profile, humans = d.get("text"), d.get("profile", "iso"), humans_of(d.get("humans"))
                    essence = d.get("primary_essence") or None
                else:
                    f = self.form(raw)
                    text, profile, humans = f.get("text"), f.get("profile", "iso"), humans_of(f.get("humans"))
                    essence = f.get("primary_essence") or None
            except (ValueError, AttributeError) as e:
                return self.send_json({"error": f"bad request: {e}"}, 400)
            if not isinstance(text, str):
                return self.send_json({"error": "field \"text\" (string) is required"}, 400)
            if profile not in ("iso", "opcloud") or essence not in (None, "physical", "informatical"):
                return self.send_json({"error": "profile: iso | opcloud; primary_essence: physical | informatical"},
                                      400)
            self.send_json(run_check(text, profile, humans, essence))
        else:
            self.send("<!doctype html><meta charset=\"utf-8\"><title>Nenalezeno</title><p>Stránka není.</p>",
                      status=404)


class Server(ThreadingHTTPServer):
    # no SO_REUSEADDR: on Windows it lets a second server bind the same port silently
    allow_reuse_address = False
    daemon_threads = True


def server(port: int = PORT) -> ThreadingHTTPServer:
    """The server on 127.0.0.1 (port 0 = any free port); the caller runs serve_forever()."""
    return Server(("127.0.0.1", port), Handler)


def warm_up() -> None:
    """Load the catalogue and the shapes of both profiles, so the first check on the page is quick."""
    for profile in ("iso", "opcloud"):
        run_check("", profile, None)


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m opmvalid.web", description="Web page: OPL -> RDF and SHACL report.")
    ap.add_argument("--port", type=int, default=PORT, help=f"port on 127.0.0.1 (default {PORT})")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    a = ap.parse_args(argv)
    try:
        srv = server(a.port)
    except OSError:
        print(f"Port {a.port} is in use: the web page is probably already running, open http://127.0.0.1:{a.port}/ "
              f"(or start with --port <other>).")
        return 1
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"opmvalid web: {url}  (stop with Ctrl+C)", flush=True)
    threading.Thread(target=warm_up, daemon=True).start()
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
