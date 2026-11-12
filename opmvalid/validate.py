"""Validation of OPL text: parse (syntax), convert to RDF, run the SHACL shapes, and report why and where.

Report entries: code (catalogue id), severity E / W / I, line, column, sentence, message,
ISO clause, hint. Messages and hints come from the catalogue (katalog/pravidla.yaml); the SHACL engine only finds
the focus. One cause gives one entry: link-kind errors caused by a thing used both as object and process are
reported once, by OPM-7-009.

Profiles: "iso" (default; shapes iso + both; OPCloud wordings reported as I) and "opcloud" (shapes opcloud + both).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from pyshacl import validate as shacl_validate
from rdflib import RDF, BNode, Graph, Literal, Namespace

from . import config, parser, rdf, syntax

OPM = Namespace(config.OPM_NS)
SH = Namespace("http://www.w3.org/ns/shacl#")
CATALOGUE = config.HERE.parent / "katalog" / "pravidla.yaml"
SHAPES = config.HERE / "tvary.ttl"
SEVERITY = {SH.Violation: "E", SH.Warning: "W", SH.Info: "I"}
KIND_RULES = {"OPM-9-001", "OPM-9-021", "OPM-9-087", "OPM-10-001"}
PARAGRAPH_RULES = {"OPM-14-051", "OPM-14-061", "OPM-14-062", "OPM-14-067", "OPM-14-068", "OPM-14-069",
                   "OPM-14-086", "OPM-14-091", "OPM-14-094", "OPM-B-002"}
# rules about a missing fact: the fact may be in a sentence the grammar could not read
ABSENCE_RULES = {"OPM-8-002", "OPM-8-008", "OPM-8-010", "OPM-9-036", "OPM-10-050", "OPM-13-001", "OPM-14-062",
                 "OPM-14-094"}
INTERNAL_HINT = "This is a defect of the validator, not of the model; please report the sentence."


@dataclass
class Entry:
    code: str
    severity: str
    line: int | None
    column: int | None
    sentence: str | None
    message: str
    iso: str | None = None
    hint: str | None = None


@lru_cache(maxsize=1)
def catalogue() -> dict:
    data = yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))
    return {r["id"]: r for r in data["rules"]}


@lru_cache(maxsize=2)
def shapes(profile: str) -> tuple:
    g = Graph().parse(SHAPES)
    drop = {s for s, p in g.subject_objects(OPM.profile) if str(p) not in ("both", profile)}
    for s in drop:
        for t in list(g.triples((s, None, None))):
            g.remove(t)
    rule_of = {}
    for s, r in g.subject_objects(OPM.rule):
        todo = [s]
        while todo:                                  # the shape and every blank node under it
            n = todo.pop()
            if n in rule_of:
                continue
            rule_of[n] = str(r)
            todo += [o for o in g.objects(n, None) if isinstance(o, BNode)]
    onto = Graph().parse(config.ONTOLOGY)
    return g, onto, rule_of


PROCEDURAL = {OPM.ConsumptionLink: "consumption", OPM.ResultLink: "result", OPM.EffectLink: "effect",
              OPM.AgentLink: "agent", OPM.InstrumentLink: "instrument"}


AND_FORMS = [(OPM.ChangeLink, "change (changes)"), (OPM.ConsumptionLink, "consumption (consumes)"),
             (OPM.ResultLink, "result (yields)"), (OPM.EffectLink, "effect (affects)"),
             (OPM.AgentLink, "agent (handles)"), (OPM.InstrumentLink, "instrument (requires)")]
# rules reported once per sentence (several links of one sentence share the cause)
PER_SENTENCE = {"OPM-12-002"}
# verbs of the joined sentence in the hint of OPM-12-002 (12.1; ChangeLink before EffectLink)
JOIN_VERB = [(OPM.ChangeLink, "changes"), (OPM.ConsumptionLink, "consumes"), (OPM.ResultLink, "yields"),
             (OPM.EffectLink, "affects"), (OPM.InstrumentLink, "requires"), (OPM.AgentLink, "handle")]


class Filler(dict):
    """Values for the catalogue message; .at is the sentence (or OPD heading) the entry is reported at."""
    at = None

    def __missing__(self, key):
        return "…"


def name(g: Graph, node) -> str:
    if node is None:
        return "…"
    if isinstance(node, Literal):
        return str(node)
    return str(g.value(node, OPM.name) or g.value(node, OPM.label) or node).strip()


def article(words: str) -> str:
    return ("an " if words[:1].lower() in "aeiou" else "a ") + words


def joined(g: Graph, links: list, process: str) -> str:
    """One sentence joining the links with "and" (12.1), as an example in the hint of OPM-12-002."""
    def phrase(link) -> str:
        st, fr, to = (g.value(link, p) for p in (OPM.state, OPM.fromState, OPM.toState))
        return (f"{name(g, st)} " if st is not None else "") + name(g, g.value(link, OPM.object)) \
            + (f" from {name(g, fr)}" if fr is not None else "") + (f" to {name(g, to)}" if to is not None else "")
    verb = next((w for k, w in JOIN_VERB if (links[0], RDF.type, k) in g), None)
    things = " and ".join(phrase(link) for link in links)
    if verb is None:
        return "…"
    return f"{things} handle {process}." if verb == "handle" else f"{process} {verb} {things}."


def sentence_of(g: Graph, node, rule: str | None = None):
    """Sentence node of a link, fan or in-zoom; for a thing or state the first sentence of a link using it."""
    if rule == "OPM-7-017" and g.value(node, OPM.propertySentence) is not None:
        return g.value(node, OPM.propertySentence)
    s = g.value(node, OPM.sentence)
    if s is not None:
        return s
    users = [l for p in (OPM.object, OPM.process, OPM.source, OPM.target, OPM.state, OPM.fromState, OPM.toState,
                         OPM.sourceState, OPM.targetState) for l in g.subjects(p, node)]
    if (node, RDF.type, OPM.State) in g:
        own = [s for p in (OPM.valueSentence, OPM.markSentence) for s in g.objects(node, p)]
        if not users and own:                         # a state only marked, or only given as a value
            return min(own, key=lambda s: position(g, s))
        owner = g.value(node, OPM.stateOf)
        users = users or [l for l in g.subjects(OPM.object, owner)]
    sentences = [g.value(l, OPM.sentence) for l in users if g.value(l, OPM.sentence) is not None]
    if (node, RDF.type, OPM.OPD) in g:
        sentences = list(g.subjects(OPM.heads, node))  # an OPD is reported at its paragraph heading
    sentences = sentences or [s for p in (OPM.objectSentence, OPM.processSentence) for s in g.objects(node, p)]
    sentences = sentences or [g.value(z, OPM.sentence) for z in g.subjects(OPM.refines, node)   # only in-zoomed
                              if (z, RDF.type, OPM.InZoom) in g and g.value(z, OPM.sentence) is not None]
    return min(sentences, key=lambda s: position(g, s), default=None)


def position(g: Graph, s) -> tuple:
    return int(g.value(s, OPM.line)), int(g.value(s, OPM.column))


def links_between(g: Graph, obj, process) -> list:
    """Procedural links between an object and a process, in text order, grouped by OPD."""
    links = [l for l in g.subjects(OPM.object, obj) if g.value(l, OPM.process) == process
             and any((l, RDF.type, k) in g for k in PROCEDURAL)]
    groups = defaultdict(list)
    for l in sorted(links, key=lambda l: position(g, g.value(l, OPM.sentence))):
        groups[g.value(l, OPM.inOPD)].append(l)
    return list(groups.values())


def kind_of(g: Graph, link) -> str:
    return next(w for k, w in PROCEDURAL.items() if (link, RDF.type, k) in g)


def pair(f: Filler, g: Graph, s1, s2):
    """Two sentences of one problem: both go into the message, the later one is where it is reported."""
    if s1 is None or s2 is None:
        return
    f.at = max(s1, s2, key=lambda s: position(g, s))
    f["sentence1"], f["line1"] = str(g.value(s1, OPM.text)), str(g.value(s1, OPM.line))
    f["sentence2"], f["line2"] = str(g.value(s2, OPM.text)), str(g.value(s2, OPM.line))


def context(g: Graph, focus, value, rule: str | None = None) -> Filler:
    f = Filler()
    if isinstance(value, Literal):
        v = str(value)
        f["property"] = "affiliation" if v in ("systemic", "environmental") else "essence"
    s = f.at = sentence_of(g, focus, rule)
    if s is not None:
        f["sentence"] = str(g.value(s, OPM.text))
        f["line"] = str(g.value(s, OPM.line))
    if (focus, RDF.type, OPM.Link) in g or (focus, RDF.type, OPM.Fan) in g:
        for key, prop in (("object", OPM.object), ("process", OPM.process), ("source", OPM.source),
                          ("target", OPM.target)):
            if g.value(focus, prop) is not None:
                f[key] = name(g, g.value(focus, prop))
        st = g.value(focus, OPM.state) or g.value(focus, OPM.fromState) or g.value(focus, OPM.toState)
        if st is not None:
            f["state"] = name(g, st)
        wrong = [g.value(focus, p) for p in (OPM.process, OPM.object, OPM.source, OPM.target)
                 if g.value(focus, p) is not None]
        both = [w for w in wrong if (w, RDF.type, OPM.Object) in g and (w, RDF.type, OPM.Process) in g]
        if both:
            f["thing"], f["kind"] = name(g, both[0]), "thing used both as an object and as a process"
    elif (focus, RDF.type, OPM.State) in g:
        owner = g.value(focus, OPM.stateOf)
        f["state"], f["object"] = name(g, focus), name(g, owner)
        declared = sorted(name(g, s) for s in g.subjects(OPM.stateOf, owner) if g.value(s, OPM.declared))
        f["states"] = ", ".join(declared) or inherited_values(g, owner) or "none declared"
        # "consumes collection Kit" where "Collection Kit" is a thing of the model: probably a name in lower case
        whole = " ".join(w[:1].upper() + w[1:] for w in f["state"].split()) + " " + f["object"]
        if (None, OPM.name, Literal(whole)) in g and not g.value(focus, OPM.declared):
            f["states"] += f' (if the thing "{whole}" is meant, write each word of its name with a capital letter)'
    else:
        n = name(g, focus)
        f["thing"] = f["object"] = f["process"] = f["opd"] = f["exhibitor"] = f["feature"] = f["name"] = n
        if value is not None:
            f["value"] = f["n"] = name(g, value)
            f["process"] = name(g, value) if (focus, RDF.type, OPM.Object) in g else n
    specific(f, g, focus, value, rule)
    return f


def inherited_values(g: Graph, attr) -> str:
    """The values an attribute inherits from the generals and classes of its exhibitor, for the hint of OPM-9-036."""
    fname, todo, seen = g.value(attr, OPM.featureName), [g.value(attr, OPM.featureOf)], set()
    while fname is not None and todo:
        thing = todo.pop()
        for anc in [a for p in (OPM.specializes, OPM.instanceOf) for a in g.objects(thing, p) if a not in seen]:
            seen.add(anc)
            todo.append(anc)
            values = sorted(name(g, s) for a in g.subjects(OPM.featureOf, anc) if g.value(a, OPM.featureName) == fname
                            for s in g.subjects(OPM.stateOf, a) if g.value(s, OPM.declared))
            if values:
                return f'{", ".join(values)} (the values of "{fname} of {name(g, anc)}")'
    return ""


REFINEMENT = {OPM.hasPart: "part", OPM.hasFeature: "feature", OPM.hasSpecialization: "specialization",
              OPM.hasInstance: "instance"}


def line_of(g: Graph, s) -> str:
    return str(g.value(s, OPM.line))


def specific(f: Filler, g: Graph, focus, value, rule: str | None):
    """Values that only some catalogue messages use, and where these rules are reported."""
    n = name(g, focus)
    if rule == "OPM-7-009":
        pair(f, g, g.value(focus, OPM.objectSentence), g.value(focus, OPM.processSentence))
    elif rule == "OPM-14-081":                            # value = the process the object has two roles for
        links = sorted((l for group in links_between(g, focus, value) for l in group),
                       key=lambda l: position(g, g.value(l, OPM.sentence)))
        other = next((l for l in links if kind_of(g, l) != kind_of(g, links[0])), links[0])
        pair(f, g, g.value(links[0], OPM.sentence), g.value(other, OPM.sentence))
    elif rule == "OPM-8-004":                             # value = the process linked more than once
        group = next(gr for gr in links_between(g, focus, value)
                     if len({(kind_of(g, l), g.value(l, OPM.linkSpec)) for l in gr}) > 1)
        f["links"] = ", ".join(f"{kind_of(g, l)} in line {line_of(g, g.value(l, OPM.sentence))}" for l in group)
        f.at = g.value(group[1], OPM.sentence)
    elif rule in ("OPM-7-023", "OPC-001"):                # more than one default / final state
        mark = OPM.isDefault if rule == "OPM-7-023" else OPM.isFinal
        marked = [st for st in g.subjects(OPM.stateOf, focus) if g.value(st, mark) is not None]
        f["n"], f["states"] = str(len(marked)), ", ".join(sorted(name(g, st) for st in marked))
        marks = [g.value(st, OPM.markSentence) for st in marked if g.value(st, OPM.markSentence) is not None]
        f.at = max(marks, key=lambda s: position(g, s), default=f.at)
    elif rule == "OPM-7-006":                             # value = the duration
        f["value"] = str(value)
        exc = [l for l in g.subjects(OPM.source, focus) if g.value(l, OPM.duration) == value]
        f.at = g.value(exc[0], OPM.sentence) if exc else g.value(focus, OPM.durationSentence) or f.at
    elif rule == "OPM-9-170":                             # the largest minimum and the smallest maximum
        f["min"], f["max"] = str(g.value(focus, OPM.durationsShown) or "… | …").split(" | ")
        f["process"] = n
        f.at = g.value(focus, OPM.durationsSentence) or f.at
    elif rule == "OPM-11-023":                            # focus = the range node
        f["range"] = str(g.value(focus, OPM.text))
    elif rule == "OPM-B-007":
        words = n.split(" of ")[0].split()               # an operation "Heating of Kettle" is named "Heating"
        f["problem"] = " and ".join(p for p, bad in (("does not end in -ing", not words[-1].endswith("ing")),
                                                     (f"has {len(words)} words", len(words) > 4)) if bad)
        f.at = g.value(focus, OPM.processSentence) or f.at
    elif rule == "OPM-10-037":                            # focus = the exhibition link
        target = g.value(focus, OPM.target)
        f["thing"], f["exhibitor"] = name(g, target), name(g, g.value(focus, OPM.source))
        f["role"] = f"an {g.value(focus, OPM.featureRole)}"
        f["kind"] = "a process" if (target, RDF.type, OPM.Process) in g else "an object"
    elif rule == "OPM-10-050":                            # value = the exhibitor named after "of"
        f["feature"], f["exhibitor"] = str(g.value(focus, OPM.featureName)), name(g, value)
    elif rule == "OPM-10-070":                            # value = the value given to the feature
        fname = g.value(focus, OPM.featureName)
        todo, seen = [g.value(focus, OPM.featureOf)], set()
        while todo:                                       # the class (or general) that gives the range
            anc = todo.pop()
            seen.add(anc)
            attr = next((a for a in g.subjects(OPM.featureOf, anc) if g.value(a, OPM.featureName) == fname
                         and g.value(a, OPM.rangeMin) is not None), None)
            if attr is not None:
                f["attribute"], f["class"] = str(fname), name(g, anc)
                f["min"], f["max"] = str(g.value(attr, OPM.rangeMin)), str(g.value(attr, OPM.rangeMax))
                break
            todo += [a for p in (OPM.specializes, OPM.instanceOf) for a in g.objects(anc, p) if a not in seen]
        f["value"] = str(value)
    elif rule == "OPM-10-068":                            # focus, value = two specializations of one general
        f["special1"], f["special2"] = name(g, value), n
        f["general"] = name(g, g.value(focus, OPM.specializes))
        f["values"] = ", ".join(sorted(f"{g.value(a, OPM.featureName)} {v}" for a in g.subjects(OPM.featureOf, focus)
                                       for v in g.objects(a, OPM.value)))
        f.at = g.value(focus, OPM.objectSentence) or g.value(focus, OPM.processSentence) or f.at
    elif rule == "OPM-10-085":
        f["object"] = name(g, g.value(focus, OPM.featureOf))
        f["attribute"] = str(g.value(focus, OPM.featureName))
        f["values"] = ", ".join(sorted(str(v) for v in g.objects(focus, OPM.value)))
    elif rule == "OPM-12-022":                            # focus = the fan
        end = g.value(focus, OPM.convergentEnd)
        bare = [l for l in g.objects(focus, OPM.member) if g.value(l, OPM.probability) is None]
        f["thing"] = ", ".join(sorted(name(g, next(e for e in (g.value(l, OPM.object), g.value(l, OPM.process))
                                                   if e != end)) for l in bare))
    elif rule == "OPM-12-023":
        f["sum"] = f"{float(value):g}".replace(".", ",")
    elif rule == "OPM-12-002":                            # value = the link of the earlier sentence
        s0 = g.value(value, OPM.sentence)
        f["sentence0"], f["line0"] = str(g.value(s0, OPM.text)), line_of(g, s0)
        f["verb"] = next((w for k, w in AND_FORMS if (focus, RDF.type, k) in g), "procedural")
        f["process"] = name(g, g.value(focus, OPM.process))
        f["joined"] = joined(g, [value, focus], f["process"])
    elif rule == "OPM-13-001":                            # value = the path label
        f["label"] = str(value)
        links = [l for l in g.subjects(OPM.process, focus) if g.value(l, OPM.pathLabel) == value]
        into = any((l, RDF.type, k) in g for l in links for k in (OPM.ConsumptionLink, OPM.EffectLink, OPM.EnablingLink))
        f["direction"] = "into" if into else "out of"
        f.at = min((g.value(l, OPM.sentence) for l in links), key=lambda s: position(g, s), default=f.at)
    elif rule == "OPM-14-020":
        path = cycle(g, focus)
        f["path"] = " and ".join(f'"{name(g, t)}"' for t in path[1:-1]) or "itself"
        kinds = {next((w for p, w in REFINEMENT.items() if (a, p, b) in g), "refinee") for a, b in zip(path, path[1:])}
        f["relation"] = kinds.pop() if len(kinds) == 1 else "refinee"
        f.at = sentence_of(g, focus) or f.at
    elif rule == "OPM-14-054" and value is not None:      # value = the in-zoomed process
        if g.value(focus, OPM.source) is not None:        # invocation / exception: the event comes from a process
            f["object"], f["process"] = name(g, g.value(focus, OPM.source)), name(g, g.value(focus, OPM.target))
        f["subprocess"], f["process"] = f.get("process", "…"), name(g, value)
    elif rule == "OPM-14-055":                            # value = the in-zoomed process or the earlier subprocess
        obj = f.get("object", "…")
        if any((z, OPM.childOPD, g.value(focus, OPM.inOPD)) in g for z in g.subjects(OPM.refines, value)):
            f["detail"] = f'The change of "{obj}" is attached to the in-zoomed process "{name(g, value)}"'
        else:
            f["detail"] = (f'"{name(g, value)}" takes "{obj}" from its input state after "{f.get("process", "…")}"'
                           f" has already brought it to its output state")
    elif rule == "OPM-14-050":                            # focus = the link in the parent OPD, value = the process
        f["opd"] = name(g, g.value(focus, OPM.inOPD))
        f["child"] = ", ".join(sorted({name(g, c) for z in g.subjects(OPM.refines, value)
                                       for c in g.objects(z, OPM.childOPD)
                                       if (c, OPM.parentOPD, g.value(focus, OPM.inOPD)) in g})) or "…"
    elif rule == "OPM-14-086":                            # value = the class of the prevailing link
        obj, proc = g.value(focus, OPM.object), g.value(focus, OPM.process)
        f["opd"] = name(g, g.value(focus, OPM.inOPD))
        f["child"] = ", ".join(sorted({name(g, c) for z in g.subjects(OPM.refines, proc)
                                       for c in g.objects(z, OPM.childOPD)})) or "…"
        f["link"] = article(f"{kind_of(g, focus)} link")
        prevailing = PROCEDURAL.get(value, "…")
        f["prevailing"] = article(f"{prevailing} link")
        o, p = name(g, obj), name(g, proc)
        f["expected"] = {"consumption": f"{p} consumes {o}.", "result": f"{p} yields {o}.",
                         "effect": f"{p} affects {o}.", "agent": f"{o} handles {p}.",
                         "instrument": f"{p} requires {o}."}.get(prevailing, "…")
    elif rule == "OPM-14-091":                            # value = "condition" or "none" (what the link should be)
        obj, proc = g.value(focus, OPM.object), g.value(focus, OPM.process)
        f["opd"] = name(g, g.value(focus, OPM.inOPD))
        f["child"] = ", ".join(sorted({name(g, c) for z in g.subjects(OPM.refines, proc)
                                       for c in g.objects(z, OPM.childOPD)})) or "…"
        k = kind_of(g, focus)
        modifier = g.value(focus, OPM.modifier)
        f["kind"] = k
        f["link"] = article(f"{modifier} {k} link" if modifier else f"{k} link")
        f["inside"] = "all condition links" if str(value) == "condition" else "not all condition links"
        o, p = name(g, obj), name(g, proc)
        plain = {"consumption": f"{p} consumes {o}.", "result": f"{p} yields {o}.", "effect": f"{p} affects {o}.",
                 "agent": f"{o} handles {p}.", "instrument": f"{p} requires {o}."}
        conditional = {"consumption": f"{p} occurs if {o} exists, in which case {o} is consumed, otherwise {p} is "
                                      f"skipped.",
                       "effect": f"{p} occurs if {o} exists, in which case {p} affects {o}, otherwise {p} is skipped.",
                       "agent": f"{o} handles {p} if {o} exists, else {p} is skipped.",
                       "instrument": f"{p} occurs if {o} exists, else {p} is skipped."}
        f["expected"] = (conditional if str(value) == "condition" else plain).get(k, "…")
    elif rule == "OPM-14-061":                            # focus = an OPD of a cycle of refinement sentences
        path, node = [n], focus
        while True:
            node = next(iter(sorted(g.objects(node, OPM.parentOPD))), None)
            if node is None or name(g, node) in path:
                path.append(name(g, node))
                break
            path.append(name(g, node))
        f["path"] = " -> ".join(path)                    # each OPD followed by the parent its sentence names
    elif rule in ("OPM-14-069", "OPM-14-094"):            # focus = the refinement edge with its sentence
        thing, parent = g.value(focus, OPM.refines), g.value(focus, OPM.parent)
        f["thing"], f["parent"] = name(g, thing), name(g, parent)
        f["child"] = name(g, g.value(focus, OPM.child))
        f["zoomed"] = name(g, value)
        f["problem"] = (f'"{f["thing"]}" does not appear in {f["parent"]}' if (None, OPM.heads, parent) in g else
                        f'{f["parent"]} is not an OPD paragraph of the text')
    elif rule == "OPM-14-062":
        parents = sorted(name(g, p) for p in g.objects(focus, OPM.parentOPD))
        f["problem"] = (f"has {len(parents)} parent OPDs ({', '.join(parents)})" if parents else
                        "has no refinement sentence naming its parent OPD")
    elif rule == "OPM-14-067":                            # focus = the SD node; reported at the second SD heading
        heads = sorted((p for d in g.subjects(OPM.label, Literal("SD")) for p in g.subjects(OPM.heads, d)),
                       key=lambda p: position(g, p))
        f.at = heads[1] if len(heads) > 1 else f.at
        f["n"] = str(len(heads))
    elif rule == "OPM-14-068":                            # focus = the SD node (SHACL Core: no value)
        systemic = [p for p in g.subjects(OPM.shownIn, focus) if (p, RDF.type, OPM.Process) in g
                    and (p, RDF.type, OPM.Object) not in g and str(g.value(p, OPM.affiliation)) == "systemic"]
        f["list"] = ", ".join(sorted(name(g, p) for p in systemic)) or "none"
        f["n"] = str(len(systemic))
    elif rule == "OPM-14-088" and value is not None:      # value = the earlier link
        earlier = g.value(value, OPM.sentence)
        f["sentence1"], f["sentence2"] = str(g.value(earlier, OPM.text)), f.get("sentence", "…")
        k1 = {OPM.ConsumptionLink: "consumed", OPM.ResultLink: "created", OPM.EffectLink: "affected"}
        first = next((w for c, w in k1.items() if (value, RDF.type, c) in g), "")
        f["reason"] = ("the object no longer exists after it is consumed" if first == "consumed" else
                       "the object exists already and cannot be created again")
    elif rule == "OPM-14-093":
        zooms = sorted((z for z in g.subjects(OPM.refines, focus) if (z, RDF.type, OPM.InZoom) in g),
                       key=lambda z: position(g, g.value(z, OPM.sentence)))
        f["opds"] = " and ".join(f"line {line_of(g, g.value(z, OPM.sentence))}" for z in zooms)
        f.at = g.value(zooms[-1], OPM.sentence)
    elif rule == "OPM-B-002":
        f["n"] = str(len(set(g.subjects(OPM.shownIn, focus))))
    elif rule == "OPM-B-006":
        f["refineables"] = ", ".join(sorted(name(g, w) for w in g.subjects(OPM.hasPart, focus)))
    elif rule == "OPC-002":                               # focus = the physical subprocess (SHACL Core: value = focus)
        parent = next((p for p in g.objects(focus, OPM.subprocessOf)
                       if str(g.value(p, OPM.essence)) == "informatical"), None)
        f["subprocess"], f["process"] = n, name(g, parent)


def cycle(g: Graph, start) -> list:
    """Shortest refinement path from a thing back to itself: [start, ..., start]."""
    paths, seen = [[start]], set()
    while paths:
        path = paths.pop(0)
        for nxt in g.objects(path[-1], OPM.refinee):
            if nxt == start:
                return path + [start]
            if nxt not in seen:
                seen.add(nxt)
                paths.append(path + [nxt])
    return [start, start]


def named_in(text: str, names: list) -> bool:
    return any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text) for n in names if n)


def entries_for(g: Graph, results: Graph, rule_of: dict, unread: list = (), skipped: set | None = None,
                profile: str = "iso") -> list:
    """Entries for the SHACL results; rules about a missing fact are left out (into skipped) for things named in
    the unreadable sentences."""
    cat = catalogue()
    out, seen = [], set()
    raw = []
    for r in results.subjects(RDF.type, SH.ValidationResult):
        src = results.value(r, SH.sourceShape)
        rule = rule_of.get(src, "?")
        focus, value = results.value(r, SH.focusNode), results.value(r, SH.value)
        raw.append((rule, focus, value, SEVERITY.get(results.value(r, SH.resultSeverity), "E")))
    kind_conflicts = {f for rule, f, v, s in raw if rule == "OPM-7-009"}
    contradictions = {(f, v) for rule, f, v, s in raw if rule == "OPM-14-081"}
    doubles = {(f, v) for rule, f, v, s in raw if rule == "OPM-8-004"}
    two_sds = any(rule == "OPM-14-067" for rule, f, v, s in raw)
    for rule, focus, value, sev in raw:
        if rule in ABSENCE_RULES and unread:
            names = [name(g, focus), name(g, g.value(focus, OPM.stateOf)) if g.value(focus, OPM.stateOf) else None,
                     str(g.value(focus, OPM.label) or ""),
                     name(g, g.value(focus, OPM.refines)) if rule == "OPM-14-094" else None]
            if any(named_in(u, names) for u in unread):
                if skipped is not None:
                    skipped.add(rule)
                continue
        if rule == "OPM-14-068" and two_sds:
            continue                                  # the SD is two paragraphs: reported once by OPM-14-067
        if rule in KIND_RULES:
            ends = {g.value(focus, p) for p in (OPM.process, OPM.object, OPM.source, OPM.target)}
            if ends & kind_conflicts:
                continue                              # reported once by OPM-7-009
        if rule == "OPM-B-007" and focus in kind_conflicts:
            continue                                  # an object name used as a process: OPM-7-009
        only_both = all({kind_of(g, l) for l in group} <= {"consumption", "result"}
                        for group in links_between(g, focus, value)) if rule in ("OPM-8-004", "OPM-14-081") else None
        if rule == "OPM-8-004" and (focus, value) in contradictions and only_both:
            continue                                  # consumes and yields it: reported once by OPM-14-081
        if rule == "OPM-14-081" and (focus, value) in doubles and not only_both:
            continue                                  # two links in one OPD paragraph: reported once by OPM-8-004
        key = (rule, g.value(focus, OPM.sentence)) if rule in PER_SENTENCE else (rule, focus, value)
        if key in seen:
            continue
        seen.add(key)
        entry = cat.get(rule, {})
        ctx = context(g, focus, value, rule)
        msg = entry.get("message_" + profile) or entry.get("message") or rule
        if msg.startswith("See OPM-"):
            target = msg.split()[1].rstrip(".")
            msg = cat.get(target, {}).get("message", msg)
        s = ctx.at
        sev = entry.get("severity_" + profile) or entry.get("severity") or sev     # the catalogue decides E / W / I
        if rule == "OPM-B-002" and value is not None:
            ctx["n"] = name(g, value)
        line, column = position(g, s) if s is not None else (None, None)
        out.append(Entry(rule, sev, line, column, str(g.value(s, OPM.text)) if s is not None else None,
                         " ".join(str(msg).split()).format_map(ctx),
                         str(entry.get("iso")) if entry.get("iso") else None,
                         " ".join(str(entry.get("hint", "")).split()).format_map(ctx) or None))
    return out


def catalogue_entry(code: str, line, column, sentence, fill: dict) -> Entry:
    """Entry of a check made outside SHACL (grammar, sentence wording, paragraphs), with the catalogue texts."""
    entry = catalogue().get(code, {})
    f = Filler(fill)
    msg = entry.get("message") or code
    if msg.startswith("See OPM-"):
        msg = catalogue().get(msg.split()[1].rstrip("."), {}).get("message", msg)
    return Entry(code, entry.get("severity") or "E", line, column, sentence, " ".join(str(msg).split()).format_map(f),
                 str(entry.get("iso")) if entry.get("iso") else None,
                 " ".join(str(entry.get("hint", "")).split()).format_map(f) or None)


def defects(g: Graph) -> list:
    """Wording defects found while converting a readable sentence (rdf.Builder.defect)."""
    out = []
    for d in g.subjects(RDF.type, OPM.Defect):
        s = g.value(d, OPM.sentence)
        line, column = position(g, s)
        text = str(g.value(s, OPM.text))
        fill = {"sentence": text, "detail": str(g.value(d, OPM.detail))}
        if " invokes " in text:                       # OPM-9-129 hint: "<Process> invokes itself."
            fill["process"] = text.split(" invokes ")[0]
        out.append(catalogue_entry(str(g.value(d, OPM.violates)), line, column, text, fill))
    return out


def check(text: str, profile: str = "iso", humans: set | None = None, primary_essence: str | None = None) -> dict:
    doc = parser.read(text)
    if not doc.sentences:                             # empty input is an error, nothing else
        e = Entry("EMPTY", "E", None, None, None, "The input contains no OPL sentences.", "Annex A",
                  "Check that the right file or text was given; an OPL specification consists of sentences.")
        return {"profile": profile, "sentences": 0, "unreadable": 0, "errors": 1, "warnings": 0,
                "entries": [asdict(e)]}
    entries = []
    for s in doc.sentences:                           # syntax (grammar)
        if not s.ok:
            code, fill = syntax.explain(s)
            if code == "SYNTAX":
                entries.append(Entry("SYNTAX", "E", s.line, s.error["column"], s.text,
                                     f"Not a valid OPL sentence: {fill['detail']}.", "Annex A", None))
            else:
                entries.append(catalogue_entry(code, s.line, s.error["column"], s.text, fill))
        elif "opcloud" in s.dialect and profile == "iso":
            entries.append(Entry("DIALECT", "I", s.line, s.column, s.text,
                                 "OPCloud wording, not a form of ISO 19450.", "Annex A", None))
        elif "annex_a" in s.dialect:
            entries.append(Entry("DIALECT", "I", s.line, s.column, s.text,
                                 "Form found only in Annex A; the clause text of the norm writes it differently.",
                                 "Annex A", None))
        elif "figure" in s.dialect and profile == "iso":
            entries.append(Entry("DIALECT", "I", s.line, s.column, s.text,
                                 "Form found only in an example of the norm (Figure 47: one agent sentence for "
                                 "several processes); 9.2.2 and Annex A give one process per agent sentence.",
                                 "9.2.2", None))
    for code, line, column, heading, fill in syntax.document(doc, profile):
        entries.append(catalogue_entry(code, line, column, heading, fill))
    try:
        g = rdf.convert(doc, humans, primary_essence)
    except Exception as ex:  # noqa: BLE001  a defect of the tool is reported, never shown as a traceback
        entries.append(Entry("INTERNAL", "E", None, None, None,
                             f"The model could not be converted to RDF ({type(ex).__name__}: {ex}); the rules were "
                             "not checked.", None, INTERNAL_HINT))
        entries.sort(key=lambda e: (e.line or 0, e.column or 0, e.code))
        return {"profile": profile, "sentences": len(doc.sentences),
                "unreadable": sum(not s.ok for s in doc.sentences),
                "errors": sum(e.severity == "E" for e in entries),
                "warnings": sum(e.severity == "W" for e in entries), "entries": [asdict(e) for e in entries]}
    entries += defects(g)
    unconverted = []
    for s, why in sorted(g.subject_objects(OPM.unconverted), key=lambda x: position(g, x[0])):
        line, column = position(g, s)
        unconverted.append(str(g.value(s, OPM.text)))
        entries.append(Entry("INTERNAL", "E", line, column, unconverted[-1],
                             f"The sentence was read, but could not be converted to RDF ({why}); it was not checked.",
                             None, INTERNAL_HINT))
    shape_graph, onto, rule_of = shapes(profile)
    conforms, results, _ = shacl_validate(g, shacl_graph=shape_graph, ont_graph=onto, inference="none",
                                          allow_warnings=True)
    unread, skipped = [s.text for s in doc.sentences if not s.ok] + unconverted, set()
    found = entries_for(g, results, rule_of, unread, skipped, profile)
    if skipped:
        entries.append(Entry("NOT-CHECKED", "I", None, None, None,
                             f"Rules about a missing fact ({', '.join(sorted(skipped))}) are not reported for things "
                             "named in unreadable sentences.", "Annex A", "Correct the unreadable sentences first."))
    if doc.paragraphs and any(e.code == "OPM-14-076" for e in entries):
        found = [e for e in found if e.code not in PARAGRAPH_RULES]   # sentences may be in the wrong paragraph
        entries.append(Entry("NOT-CHECKED", "I", None, None, None,
                             "A paragraph label is unreadable (OPM-14-076), so the sentences after it belong to the "
                             "previous paragraph: rules about paragraph contents were not checked.",
                             "14.2.2.6", "Correct the paragraph label first."))
    if not doc.paragraphs:
        found = [e for e in found if e.code not in PARAGRAPH_RULES]
        entries.append(Entry("NOT-CHECKED", "I", None, None, None,
                             "No OPD paragraphs (SD:, SD1:): rules of clause 14 that need them were not checked.",
                             "14.2.2.6", "Divide the text into OPD paragraphs to check them."))
    if humans is None:
        entries.append(Entry("NOT-CHECKED", "I", None, None, None,
                             "No list of humans given: agent and instrument rules (OPM-9-028, OPM-9-032) not checked.",
                             "9.2.2", "Give the humans with --humans."))
    entries += found
    entries.sort(key=lambda e: (e.line or 0, e.column or 0, e.code))
    return {"profile": profile, "sentences": len(doc.sentences),
            "unreadable": sum(not s.ok for s in doc.sentences),
            "errors": sum(e.severity == "E" for e in entries), "warnings": sum(e.severity == "W" for e in entries),
            "entries": [asdict(e) for e in entries]}


def format_text(report: dict, limit: bool = True) -> str:
    """The report as text (command line, web page). limit: at most 5 entries of one W/I code and 20 of one E code."""
    lines, shown = [], defaultdict(int)
    errors = {x["code"] for x in report["entries"] if x["severity"] == "E"}
    for e in report["entries"]:
        shown[e["code"]] += 1
        if limit and (shown[e["code"]] > 5 and e["severity"] != "E" or shown[e["code"]] > 20):
            continue
        where = f"line {e['line']}" + (f", col {e['column']}" if e["column"] else "") if e["line"] else "model"
        lines.append(f"{e['severity']} {e['code']:<11} {where}: {e['message']}")
        if e["sentence"]:
            lines.append(f"    \"{e['sentence']}\"")
        if e["iso"] or e["hint"]:
            lines.append("    " + (f"[ISO {e['iso']}]" if e["iso"] else "[validator]")
                         + (f"  Fix: {e['hint']}" if e["hint"] else ""))
    if limit:
        for c, n in shown.items():
            cap = 20 if c in errors else 5
            if n > cap:
                lines.append(f"  … {n - cap} more {c} (all in --format json)")
    return "".join(x + "\n" for x in lines) + (f"\n{report['sentences']} sentences, {report['unreadable']} unreadable, "
                                               f"{report['errors']} errors, {report['warnings']} warnings "
                                               f"(profile {report['profile']})")
