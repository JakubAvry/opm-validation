"""Checks of the OPL text that the grammar itself cannot express (catalogue entries with check.by = grammar).

1. explain(sentence): an unreadable sentence is reported with the catalogue entry of the sentence form it was meant
   to be, recognised by its reserved words ("consumes" -> OPM-9-006 consumption sentence, "initiates ..., which
   yields" -> OPM-8-013 no result event, ...), with where reading stopped and what was expected there. A name or a
   state written with the wrong capitals (OPM-7-004, OPM-7-021) is recognised by reading the sentence again with
   those words re-capitalised. Nothing is repaired: the sentence stays unread and is not converted;
   the second reading only chooses the explanation. Without a recognisable form the code is SYNTAX.
2. document(doc, profile): the root paragraph label (OPM-14-066), the title of a whole-system specification
   (OPM-14-078, W, ISO profile; OPCloud exports have none) and the breadth-first order of paragraphs (OPM-14-079, W).

Checks inside a readable sentence (the same object twice in an event sentence, "P invokes P") are made while
converting it (rdf.Builder.same_object, opm:Defect); links with AND semantics in separate sentences (OPM-12-002) are
a SHACL shape.
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import parser

# (pattern on the sentence, catalogue id): the first match names the intended form; specific forms first.
COND = r"(?:\boccurs if\b|^If\b|\brequires that\b)"
FORMS = [
    (r"^SD[\d.]*\b", "OPM-14-076"),                         # a paragraph label the input format does not accept
    (r"\binitiates and handles\b", "OPM-9-099"),
    (r"\binitiates\b.*\bwhich\s+yields\b", "OPM-8-013"),
    (r"\binitiates\b.*\bwhich\s+(?:consists|exhibits|relates|are)\b", "OPM-6-015"),
    (r"\binitiates\b.*\bwhich\s+consumes\b", "OPM-9-092"),
    (r"\binitiates\b.*\bwhich\s+affects\b", "OPM-9-096"),
    (r"\binitiates\b.*\bwhich\s+changes\b", "OPM-9-109"),
    (r"\binitiates\b.*\bwhich\s+requires\b", "OPM-9-102"),
    (r"\boccurs if duration of\b.*\bfalls short\b", "OPM-9-168"),
    (r"\boccurs if duration of\b", "OPM-9-165"),
    (COND + r".*\byields\b", "OPM-8-013"),
    (COND + r".*\b(?:consists of|exhibits)\b", "OPM-6-015"),
    (COND + r".*\b(?:is consumed|consumes)\b", "OPM-9-132"),
    (COND + r".*\baffects\b", "OPM-9-135"),
    (COND + r".*\bchanges\b", "OPM-9-147"),
    (r"\bhandles\b.*\bif\b|^If\b.*\bhandles\b", "OPM-9-138"),
    (COND, "OPM-9-141"),
    (r"\bwith probability\b", "OPM-12-025"),
    (r"\b[Ee]xactly one of\b|^One of\b", "OPM-12-009"),
    (r"\b[Aa]t least one of\b", "OPM-12-012"),
    (r"\bzooms\b", "OPM-14-035"),
    (r"\bis refined by unfolding\b", "OPM-14-073"),
    (r"\bis refined by\b", "OPM-14-072"),
    (r"(?:\b|-)unfolds\b", "OPM-14-030"),
    (r"\bconsumes\b", "OPM-9-006"),
    (r"\byields\b", "OPM-9-014"),
    (r"\baffects\b", "OPM-9-020"),
    (r"\bchanges\b", "OPM-9-058"),
    (r"\bhandles?\b", "OPM-9-031"),
    (r"\brequires\b", "OPM-9-035"),
    (r"\binvokes\b", "OPM-9-125"),
    (r"\bconsists of\b", "OPM-10-029"),
    (r"\bexhibits\b", "OPM-10-041"),
    (r"\b(?:is an instance of|are instances of|are an instance of)\b", "OPM-10-072"),
    (r"\branges? from\b|\brange is\b", "OPM-11-022"),
    (r"\brelates? to\b", "OPM-10-010"),
    (r"\bare related\b", "OPM-10-019"),
    (r"\b(?:or|and) other states\b", "OPM-14-017"),
]
PATH = re.compile(r"^Following path\b")
PATH_PREFIX = re.compile(r"^Following path [\w\-]+, ")
WORD = re.compile(r"[A-Za-z][\w'’\-]*")
STATE_BEFORE = {"from", "to", "be", "or", "state", "is"}     # words after which a state stands


@lru_cache(maxsize=1)
def reserved() -> frozenset:
    """The literal words of the grammar (consumes, of, from, ...): never part of a name or a state run."""
    words = set()
    for t in parser.grammar().terminals:
        p = t.pattern
        if type(p).__name__ == "PatternStr" and re.fullmatch(r"[A-Za-z\-]+", p.value):
            words.add(p.value.lower())
    return frozenset(words)


def readable(text: str) -> bool:
    s = parser.parse_sentence(text)
    return s.ok and parser.verb_tag(s) is None        # "handles blood" is not a tag (parser.verb_tags)


def recapitalised(text: str):
    """(rule, wrong words, corrected sentence) when re-capitalising words makes the sentence readable."""
    words = list(WORD.finditer(text))
    # a capitalised state after from / to / be / or / state / is (OPM-7-021)
    runs, i = [], 1
    while i < len(words):
        if words[i].group()[0].isupper() and words[i - 1].group().lower() in STATE_BEFORE:
            j = i
            while j + 1 < len(words) and words[j + 1].group()[0].isupper() \
                    and words[j + 1].group().lower() not in reserved():
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    if runs:
        fixed = list(text)
        for a, b in runs:
            for w in words[a:b + 1]:
                fixed[w.start()] = w.group()[0].lower()
        fixed = "".join(fixed)
        if readable(fixed):
            wrong = ", ".join(text[words[a].start():words[b].end()] for a, b in runs)
            return "OPM-7-021", wrong, fixed
    # a name in lower case (OPM-7-004): a run of lower-case words that are not reserved words
    runs, cur = [], []
    for k, w in enumerate(words):
        if w.group()[0].islower() and w.group().lower() not in reserved():
            cur.append(k)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    for chosen in ([k for r in runs for k in r], *runs):
        if not chosen:
            continue
        fixed = list(text)
        for k in chosen:
            fixed[words[k].start()] = words[k].group()[0].upper()
        fixed = "".join(fixed)
        if readable(fixed):
            first, last = words[chosen[0]], words[chosen[-1]]
            return "OPM-7-004", text[first.start():last.end()], fixed
    return None


def explain(s: parser.Sentence) -> tuple:
    """(code, values for the catalogue message) for an unreadable sentence."""
    near = s.error.get("near", "")
    where = f'near "{near.strip()[:25]}"' if near.strip() else "at the end"
    detail = f"reading stopped at column {s.error['column']}, {where}; expected {', '.join(s.error['expected'][:8])}"
    fill = {"sentence": s.text, "detail": detail}
    if re.search(r"\bis by default\b", s.text):       # Annex C, Figure C.4 (informative): not a form of the norm
        fill["detail"] = ('"is by default" is not a sentence form of ISO 19450 (it occurs only in the informative '
                          'Annex C); for a value write "<Attribute> of <Object> is <value>"')
        return "SYNTAX", fill
    found = recapitalised(s.text)
    if found:
        rule, wrong, fixed = found
        fill.update({"name": wrong, "state": wrong, "suggestion": fixed})
        return rule, fill
    text = s.text
    if PATH.match(text):
        if not PATH_PREFIX.match(text):
            return "OPM-13-003", fill
        text = PATH_PREFIX.sub("", text)
    for pattern, rule in FORMS:
        if re.search(pattern, text):
            if rule == "OPM-14-076":
                label = text.split()[0]
                fill["detail"] = f'"{text}" is neither a sentence nor a paragraph label; write "{label}: <name>"'
            return rule, fill
    return "SYNTAX", fill


def depth_key(label: str) -> tuple:
    """Breadth-first position of an OPD label: SD < SD1 < SD2 < ... < SD1.1 < SD1.2 < SD2.1."""
    nums = [int(n) for n in re.findall(r"\d+", label)]
    return len(nums), nums


def document(doc: parser.Document, profile: str) -> list:
    """[(code, line, column, text, fill)] for the checks over the paragraphs of the whole text."""
    out = []
    if not doc.paragraphs:
        return out
    labels = [p[0] for p in doc.paragraphs]
    first_label, first_name, first_line = doc.paragraphs[0]
    heading = f"{first_label}: {first_name}" if first_name else first_label
    if "SD" not in labels:
        out.append(("OPM-14-066", first_line, 1, heading, {"label": first_label}))
    if profile == "iso" and doc.title is None:
        out.append(("OPM-14-078", first_line, 1, heading, {}))
    expected = sorted(labels, key=depth_key)
    for (label, name, line), want in zip(doc.paragraphs, expected):
        if label != want:
            out.append(("OPM-14-079", line, 1, f"{label}: {name}" if name else label,
                        {"detail": f"{label} comes before {want}"}))
            break
    return out
