"""Reading OPL text: paragraphs, sentences, and the parse of each sentence by the grammar opl.lark.

The parser never repairs or guesses: a sentence the grammar cannot read is reported with
its line, the column where reading stopped and what was expected there; the rest of the model is read on.

Input: plain OPL text, one or more sentences per line, optionally divided into OPD
paragraphs. Accepted paragraph labels: "SD: name", "SD1: name" (Table 26 of the norm) and "# SD" (OPCloud
export). Optional title "OPL specification of <System>" and "End of OPL specification of <System>".
Leading sentence numbers ("12. ") of OPCloud exports are dropped.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from lark import Lark, Tree
from lark.exceptions import UnexpectedEOF, UnexpectedInput

HERE = Path(__file__).resolve().parent
PARAGRAPH = re.compile(r"^\s*(?:#\s*)?(SD\d*(?:\.\d+)*)\s*(?::\s*(.*))?$")
TITLE = re.compile(r"^\s*OPL specification of (.+?)\s*$", re.I)
END = re.compile(r"^\s*End of OPL specification of (.+?)\s*$", re.I)
NUMBERING = re.compile(r"^(\s*)\d+\.\s+")
SPLIT = re.compile(r"(?<=\.)\s+(?=[A-Z])")


@dataclass
class Sentence:
    line: int                 # 1-based line number in the input
    column: int               # 1-based column of the first character
    text: str                 # sentence without the final full stop
    opd: str | None           # OPD paragraph label (SD, SD1, ...) or None in a flat model
    tree: Tree | None = None
    error: dict | None = None
    dialect: set = field(default_factory=set)   # {"opcloud", "annex_a", "figure"}: forms outside the clause text

    @property
    def ok(self) -> bool:
        return self.tree is not None


@dataclass
class Document:
    sentences: list
    paragraphs: list          # [(label, name, line)]
    title: str | None = None


@lru_cache(maxsize=1)
def grammar() -> Lark:
    return Lark((HERE / "opl.lark").read_text(encoding="utf-8"), start="start", parser="earley",
                lexer="dynamic_complete", ambiguity="resolve", propagate_positions=True)


def dialect_of(tree: Tree) -> set:
    found = set()
    for t in tree.iter_subtrees():
        name = str(t.data)
        if name.startswith("opc_"):
            found.add("opcloud")
        elif name.startswith("ax_"):
            found.add("annex_a")
        elif name.startswith("fig_"):
            found.add("figure")               # "Payer handles P1 and P2." (Figure 47, N-061)
        elif name == "agent" and any(getattr(c, "data", None) == "verb_handle" for c in t.children):
            agents = next((c for c in t.children if getattr(c, "data", None) == "obj_list"), None)
            objs = [c for c in agents.children if getattr(c, "data", None) == "obj"] if agents is not None else []
            if len(objs) == 1 and not plural(objs[0]):
                found.add("annex_a")          # "Agent handle Process" (N-015)
    return found


def plural(obj: Tree) -> bool:
    """An object with a multiplicity of more than one ("k=2 to 4 Mechanics handle P", "Optional Helpers handle P"):
    the plural verb "handle" is then English, not the Annex A form."""
    for m in obj.children:
        if getattr(m, "data", None) == "mult":
            words = " ".join(str(x) for x in m.scan_values(lambda x: True)).lower()
            return words not in ("1", "one", "an optional", "at least one")
    return False


def parse_sentence(text: str, line: int = 1, column: int = 1, opd: str | None = None) -> Sentence:
    s = Sentence(line, column, text, opd)
    try:
        s.tree = grammar().parse(text)
        s.dialect = dialect_of(s.tree)
    except UnexpectedEOF as e:                 # the sentence ended too early
        s.error = {"column": column + len(text), "near": "", "expected": readable(e.expected)}
    except UnexpectedInput as e:
        pos = getattr(e, "pos_in_stream", None)
        pos = pos if isinstance(pos, int) and pos >= 0 else max(getattr(e, "column", 1), 1) - 1
        s.error = {"column": column + pos, "near": text[pos:pos + 30],
                   "expected": readable(getattr(e, "allowed", None) or getattr(e, "expected", None) or [])}
    return s


def readable(terminals) -> list:
    """Terminal names as the words a writer understands: literals as written, the rest by kind."""
    kinds = {"NAME": "<Name>", "STATE": "<state>", "TAG": "<tag>", "UNIT": "<unit>", "NUMBER": "<number>",
             "NUMWORD": "<number>", "PROP": "<physical|informatical|systemic|environmental>",
             "MARK": "<initial|final|default>", "OPD": "<OPD label>", "PROB": "<probability>", "LABEL": "<label>",
             "PARAM": "<parameter>", "UNFOLD_KIND": "<part-unfolds|...>", "MPARAM": "<parameter>",
             "MEXPR": "<expression>", "RELOP": "<relation>"}
    out = []
    for name in terminals:
        word = kinds.get(name)
        if word is None:
            try:
                word = grammar().get_terminal(name).pattern.value
            except Exception:  # noqa: BLE001
                word = name
            word = {",": ","}.get(word, word)
        if word not in out:
            out.append(word)
    return sorted(out)


def read(text: str) -> Document:
    doc = Document([], [])
    opd = None
    text = text.removeprefix("﻿")          # byte order mark of files saved by Windows editors
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        if (m := TITLE.match(raw)):
            doc.title = m.group(1)
            continue
        if END.match(raw):
            continue
        if (m := PARAGRAPH.match(raw)):
            opd = m.group(1)
            doc.paragraphs.append((opd, (m.group(2) or "").strip() or None, n))
            continue
        body = raw
        if (m := NUMBERING.match(body)):
            body = m.group(1) + body[m.end():]
        offset = len(raw) - len(body)
        start = len(body) - len(body.lstrip())
        for part in SPLIT.split(body.strip()):
            col = body.find(part, start) + 1 + offset
            start = col - offset - 1 + len(part)
            sentence = part.strip().rstrip(".").strip()
            if sentence:
                doc.sentences.append(parse_sentence(sentence, n, col, opd))
    initial_states(doc)
    verb_tags(doc)
    return doc


# the verbs of the procedural and fundamental sentences: a tag never starts with one of them
VERB_TAG = re.compile(r"^(?:handles?|requires?|consumes?|yields?|affects?|changes?|invokes?|initiates?|exhibits?)[ ]+(?=\S)")


def verb_tags(doc: Document) -> None:
    """ "Nurse handles blood Collecting." is readable as a tagged structural link with the tag "handles blood"
    (10.2.1: a tag is any phrase), so the lower-case word of the name would pass silently and the agent link would
    be missing. A tag that starts with a reserved verb of OPL followed by more lower-case words is taken as that
    verb and a wrongly written name: the sentence counts as unreadable and syntax.explain reports the name
    (OPM-7-004) or the sentence form."""
    for s in doc.sentences:
        pos = verb_tag(s)
        if pos is not None:
            s.tree, s.dialect = None, set()
            s.error = {"column": s.column + pos, "near": s.text[pos:pos + 30], "expected": ["<Name>"]}


def verb_tag(s: Sentence):
    """Position after the verb when the sentence reads as a tagged link whose tag starts with a reserved verb."""
    if not s.ok:
        return None
    node = s.tree.children[0]
    if node.data == "path_sentence":
        node = next(c for c in node.children if isinstance(c, Tree))
    if node.data != "tagged_uni":
        return None
    tag = next((c for c in node.children if not isinstance(c, Tree)), None)
    m = VERB_TAG.match(str(tag)) if tag is not None else None
    return (getattr(tag, "start_pos", None) or s.text.find(str(tag))) + m.end() if m else None


DECLARING = ("state_enum", "state_enum_open", "single_state", "state_mark", "states_mark", "ax_combined_state")
# a capitalised multi-word state before a name at the start of a sentence (after a path label)
INITIAL_STATE = re.compile(r"^(?P<path>Following path [\w\-]+, )?[A-Z][\w'’\-&]*(?:[ ]+[a-z0-9&][\w'’\-&]*)+[ ]+[A-Z#]")


def name_of(t: Tree) -> str:
    """The name a thing or feature node stands for (the same as rdf.Builder.ref)."""
    if t.data == "feature":
        return " of ".join(name_of(c) for c in t.children if isinstance(c, Tree))
    name = " ".join(str(c) for c in t.children if not isinstance(c, Tree))
    for u in t.children:
        if isinstance(u, Tree) and u.data == "unit":
            name += " in " + " ".join(str(c) for c in u.children)
    return name


def declared_states(sentences: list) -> dict:
    """Object name -> the states its state sentences name (whole text)."""
    out = {}
    for s in sentences:
        if not s.ok:
            continue
        node = s.tree.children[0]
        if node.data == "path_sentence":
            node = next(c for c in node.children if isinstance(c, Tree))
        if node.data in DECLARING:
            refs = [c for c in node.children if isinstance(c, Tree) and c.data in ("thing", "feature")]
            if refs:
                out.setdefault(name_of(refs[0]), set()).update(
                    " ".join(str(x) for x in st.children) for st in node.iter_subtrees() if st.data == "state")
    return out


def initial_states(doc: Document) -> None:
    """A state at the start of a sentence is capitalised (9.4.1 NOTE; Table 7 "Awaiting test Sample initiates
    ..."). A one-word state then reads as part of the name ("Ready Operator") and rdf.py splits it; a multi-word
    state ("On duty Nurse handles ...") makes the sentence unreadable. Such a sentence is read once more with its
    first letter in lower case and is accepted only when the object declares exactly that state somewhere in the
    text. Nothing is guessed or repaired: the state and the object are both the writer's."""
    declared = None
    for s in doc.sentences:
        m = None if s.ok else INITIAL_STATE.match(s.text)
        if not m:
            continue
        at = len(m.group("path") or "")
        again = parse_sentence(s.text[:at] + s.text[at].lower() + s.text[at + 1:], s.line, s.column, s.opd)
        if not again.ok:
            continue
        first = next((t for t in again.tree.iter_subtrees_topdown()
                      if t.data == "obj" and getattr(t.meta, "start_pos", None) == at), None)
        if first is None:
            continue
        states = [c for c in first.children if isinstance(c, Tree) and c.data == "state"]
        refs = [c for c in first.children if isinstance(c, Tree) and c.data in ("thing", "feature")]
        if declared is None:
            declared = declared_states(doc.sentences)
        if states and refs and " ".join(str(x) for x in states[0].children) in declared.get(name_of(refs[0]), ()):
            s.tree, s.error, s.dialect = again.tree, None, again.dialect


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    doc = read(Path(argv[0]).read_text(encoding="utf-8"))
    bad = [s for s in doc.sentences if not s.ok]
    for s in doc.sentences:
        if s.ok:
            print(f"{s.line:4}  ok  {s.tree.children[0].data if s.tree.children else s.tree.data}"
                  f"{'  [' + ', '.join(sorted(s.dialect)) + ']' if s.dialect else ''}  {s.text[:70]}")
        else:
            print(f"{s.line:4}  ERR col {s.error['column']}: {s.text[:70]} | expected {s.error['expected'][:6]}")
    print(f"sentences {len(doc.sentences)}, unreadable {len(bad)}, paragraphs {[p[0] for p in doc.paragraphs]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
