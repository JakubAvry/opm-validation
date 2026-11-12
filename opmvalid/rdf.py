"""OPL -> RDF: the parse trees of parser.py become a graph by the ontology ontologie.ttl.

Every link is a node with its provenance (sentence: line, column, text; OPD paragraph). After all sentences the
converter adds what follows from the whole model: the kinds of things (from their positions), default generic
properties (7.3.4), direct role properties object -> process, in-zoom order, the OPD tree and, when the input
gives it, who is a human. Inheritance is not materialised.

Usage (in the repository root): python -m opmvalid.rdf <model.opl> [--turtle out.ttl] [--humans "A, B"]
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from lark import Token, Tree
from rdflib import RDF, Graph, Literal, Namespace, XSD

from . import config, parser

OPM = Namespace(config.OPM_NS)
M = Namespace(config.MODEL_NS)

LINK_OF = {"consumption": "ConsumptionLink", "result": "ResultLink", "effect": "EffectLink", "change": "ChangeLink",
           "agent": "AgentLink", "instrument": "InstrumentLink"}
ROLE = {"ConsumptionLink": "consumedBy", "ResultLink": "yieldedBy", "EffectLink": "affectedBy",
        "ChangeLink": "affectedBy", "AgentLink": "agentOf", "InstrumentLink": "instrumentOf"}
SUPER = {"ConsumptionLink": ["TransformingLink", "ProceduralLink", "Link"],
         "ResultLink": ["TransformingLink", "ProceduralLink", "Link"],
         "EffectLink": ["TransformingLink", "ProceduralLink", "Link"],
         "ChangeLink": ["EffectLink", "TransformingLink", "ProceduralLink", "Link"],
         "AgentLink": ["EnablingLink", "ProceduralLink", "Link"],
         "InstrumentLink": ["EnablingLink", "ProceduralLink", "Link"],
         "InvocationLink": ["Link"], "ExceptionLink": ["Link"],
         "AggregationLink": ["FundamentalLink", "StructuralLink", "Link"],
         "ExhibitionLink": ["FundamentalLink", "StructuralLink", "Link"],
         "GeneralizationLink": ["FundamentalLink", "StructuralLink", "Link"],
         "ClassificationLink": ["FundamentalLink", "StructuralLink", "Link"],
         "TaggedLink": ["StructuralLink", "Link"]}
PROCESS_END = {"consumption", "result", "effect", "change", "agent", "instrument"}
NUMWORDS = {w: n for n, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}
NUMWORDS["twenty"] = 20
RANGE = re.compile(r"(\d+(?:[.,]\d+)?|[A-Za-z]+) (?:to|through) (\d+(?:[.,]\d+)?|[A-Za-z]+)")
AMOUNT = re.compile(r"^(\d+(?:[.,]\d+)?|[A-Za-z]+)\s+(\S.*)$")


def number(word: str):
    """A number written in digits ("2", "0,5") or as a word of the grammar ("two"); None for a parameter."""
    w = word.lower().replace(",", ".")
    if w in NUMWORDS:
        return Decimal(NUMWORDS[w])
    try:
        return Decimal(w)
    except ArithmeticError:
        return None


def amount(duration: str):
    """ "5 hours" -> (Decimal(5), "hour"); None when the number is a parameter."""
    m = AMOUNT.match(duration.strip())
    value = number(m.group(1)) if m else None
    return (value, m.group(2).lower().rstrip("s")) if value is not None else None


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "x"


def toks(t) -> list:
    return [str(c) for c in t.children if isinstance(c, Token)]


def words(t) -> str:
    """All tokens under t, as written (multiplicity, constraint)."""
    return " ".join(str(x) for x in t.scan_values(lambda x: isinstance(x, Token)))


def kids(t, name=None) -> list:
    return [c for c in t.children if isinstance(c, Tree) and (name is None or c.data == name)]


class Builder:
    def __init__(self, humans: set | None = None, primary_essence: str | None = None):
        self.g = Graph()
        self.g.bind("opm", OPM)
        self.g.bind("m", M)
        self.kind = defaultdict(set)          # thing name -> {"object", "process"}
        self.kind_at = {}                     # (thing name, kind) -> first sentence that uses it so
        self.things = {}
        self.states = {}
        self.n = 0
        self.humans = humans
        self.primary_essence = primary_essence
        self.stated = defaultdict(dict)       # thing -> {"essence": v, "affiliation": v}
        self.sentence = None
        self.opd = None
        self.zooms = []                       # (process, steps [[names]], interim [names], opd, child opd)
        self.path = None
        self.start = None                     # position of the sentence proper (after a path label)
        self.declared = {}                    # thing name -> declared states (whole model; for initial_state)
        self.named_elsewhere = set()          # names used not at the start of a sentence
        self.names = set()                    # every name written in the text (for a plural form, singular())
        self.values = []                      # (feature, value, sentence) of "Attribute of X is v." sentences

    # ---------------------------------------------------------------- nodes

    def new(self, prefix: str):
        self.n += 1
        return M[f"{prefix}{self.n}"]

    def thing(self, name: str, kind: str | None = None):
        node = self.things.get(name)
        if node is None:
            node = M["t_" + slug(name)]
            self.things[name] = node
            self.g.add((node, RDF.type, OPM.Thing))
            self.g.add((node, OPM.name, Literal(name)))
            if " of " in name and not name.startswith("State "):
                feat, owner = name.split(" of ", 1)
                self.g.add((node, OPM.featureOf, self.thing(owner)))
        if kind:
            self.kind[name].add(kind)
            if self.sentence is not None and (name, kind) not in self.kind_at:
                self.kind_at[(name, kind)] = self.sentence
        if self.opd is not None:
            self.g.add((node, OPM.shownIn, self.opd))
        return node

    def state(self, owner: str, name: str, declared: bool = False):
        key = (owner, name)
        node = self.states.get(key)
        if node is None:
            node = M[f"s_{slug(owner)}__{slug(name)}"]
            self.states[key] = node
            self.g.add((node, RDF.type, OPM.State))
            self.g.add((node, OPM.name, Literal(name)))
            self.g.add((node, OPM.stateOf, self.thing(owner, "object")))
        if declared:
            self.g.add((node, OPM.declared, Literal(True)))
        return node

    def link(self, cls: str, **ends):
        node = self.new("l")
        self.g.add((node, RDF.type, OPM[cls]))
        for parent in SUPER.get(cls, []):
            self.g.add((node, RDF.type, OPM[parent]))
        self.g.add((node, OPM.sentence, self.sentence))
        if self.opd is not None:
            self.g.add((node, OPM.inOPD, self.opd))
        if self.path:
            self.g.add((node, OPM.pathLabel, Literal(self.path)))
        for prop, value in ends.items():
            if value is not None:
                self.g.add((node, OPM[prop], value))
        return node

    # ---------------------------------------------------------------- tree helpers

    def ref(self, t) -> str:
        if t.data == "thing":
            name = " ".join(str(c) for c in t.children if isinstance(c, Token))
            for u in kids(t, "unit"):
                name += " in " + " ".join(toks(u))
            return name
        if t.data == "feature":
            return " of ".join(self.ref(c) for c in kids(t))
        raise ValueError(t.data)

    def is_ref(self, t) -> bool:
        return isinstance(t, Tree) and t.data in ("thing", "feature")

    def obj(self, t) -> dict:
        """obj: [mult] [state] ref [opc_at_state] [where] -> {name, mult, state, where}."""
        info = {"name": None, "mult": None, "state": None, "where": None}
        for c in kids(t):
            if c.data == "mult":
                info["mult"] = words(c)
            elif c.data == "state":
                info["state"] = " ".join(toks(c))
            elif c.data == "opc_at_state":
                info["state"] = " ".join(toks(kids(c, "state")[0]))
            elif c.data == "where":
                info["where"] = ", ".join(words(k) for k in kids(c, "constraint"))
            elif self.is_ref(c):
                info["name"] = self.ref(c)
        if info["state"] is None and info["mult"] is None and self.start is not None \
                and getattr(t.meta, "start_pos", None) == self.start:
            split = self.initial_state(info["name"])
            if split:
                info["state"], info["name"] = split
        if info["mult"] is not None:
            info["name"] = self.singular(info["name"])
        return info

    def singular(self, name: str) -> str:
        """With a multiplicity, and for the general of several specializations, OPL writes the name in plural
        ("Two Nurses handle ...", "Car, Aircraft, and Ship are Vehicles.", 11.1, Figures 25, 33). The plural is
        the same thing as its singular only when the singular is a name written elsewhere in the text or given
        in the list of humans: no English morphology is guessed, a plural without a known singular stays as it
        is."""
        head, _, last = name.rpartition(" ")
        for ending, repl in (("ies", "y"), ("es", ""), ("s", "")):
            if last.endswith(ending) and len(last) > len(ending) + 1:
                cand = (head + " " if head else "") + last[:-len(ending)] + repl
                if cand in self.names or (self.humans is not None and cand.lower() in self.humans):
                    return cand
        return name

    def initial_state(self, name: str):
        """A state at the start of a sentence is capitalised (9.4.1 NOTE): "Ready Operator handles Printing." The
        name is read as state + object only when the object declares that state and the whole name is not a thing
        used elsewhere in the model; otherwise it stays one name."""
        parts = name.split()
        if name in self.declared or name in self.named_elsewhere:
            return None
        for k in range(1, len(parts)):
            state, rest = " ".join(parts[:k]).lower(), " ".join(parts[k:])
            if state in self.declared.get(rest, ()):
                return state, rest
        return None

    def objs(self, t) -> list:
        return [self.obj(c) for c in kids(t, "obj")]

    def refs(self, t) -> list:
        return [self.ref(c) for c in kids(t) if self.is_ref(c)]

    def states_of(self, t) -> list:
        return [" ".join(toks(c)) for c in t.iter_subtrees_topdown() if c.data == "state"]

    # ---------------------------------------------------------------- procedural

    def ranges(self, mult: str):
        """Every numeric range of a multiplicity ("5 to 3", "2 through 4") as a node with its bounds (OPM-11-023)."""
        for m in RANGE.finditer(mult or ""):
            lower, upper = number(m.group(1)), number(m.group(2))
            if lower is not None and upper is not None:
                node = self.new("r")
                self.g.add((node, RDF.type, OPM.Range))
                self.g.add((node, OPM["lower"], Literal(lower)))      # OPM.lower would be the method of str
                self.g.add((node, OPM["upper"], Literal(upper)))
                self.g.add((node, OPM.text, Literal(m.group(0))))
                self.g.add((node, OPM.sentence, self.sentence))

    def object_end(self, info: dict, link, process: str | None = None):
        o = self.thing(info["name"], "object")
        self.g.add((link, OPM.object, o))
        if info["mult"]:
            self.g.add((link, OPM.multiplicity, Literal(info["mult"])))
            self.ranges(info["mult"])
        if info.get("where"):
            self.g.add((link, OPM.constraint, Literal(info["where"])))
        if info["state"]:
            self.g.add((link, OPM.state, self.state(info["name"], info["state"])))

    def procedural(self, kind: str, process: str, info: dict, modifier: str | None = None):
        cls = LINK_OF[kind]
        link = self.link(cls, process=self.thing(process, "process"))
        self.object_end(info, link)
        if modifier:
            self.g.add((link, OPM.modifier, Literal(modifier)))
            self.g.add((link, RDF.type, OPM.EventLink if modifier == "event" else OPM.ConditionLink))
        return link

    def fan(self, kind: str, links: list, convergent: str):
        node = self.new("f")
        self.g.add((node, RDF.type, OPM.Fan))
        self.g.add((node, OPM.fanKind, Literal(kind)))
        self.g.add((node, OPM.sentence, self.sentence))
        self.g.add((node, OPM.convergentEnd, self.things[convergent]))
        for link in links:
            self.g.add((node, OPM.member, link))
        return node

    def transforming(self, kind: str, t):
        """consumption / result / effect / instrument with lists and fans."""
        head = kids(t)
        if head and head[0].data in ("fan_head_or", "fan_head_xor"):          # fan of processes, one object
            fan_kind = "or" if head[0].data == "fan_head_or" else "xor"
            procs = self.refs(kids(t, "proc_alts")[0])
            info = self.obj(kids(t, "obj")[0])
            links = [self.procedural(kind, p, info) for p in procs]
            self.fan(fan_kind, links, info["name"])
            return
        process = self.ref(head[0])
        if kids(t, "any_of") or kids(t, "one_of"):
            fan_kind = "or" if kids(t, "any_of") else "xor"
            infos = self.objs(kids(t, "obj_alts")[0])
            links = [self.procedural(kind, process, i) for i in infos]
            self.fan(fan_kind, links, process)
            return
        for info in self.objs(kids(t, "obj_list")[0]):
            self.procedural(kind, process, info)

    def agent(self, t):
        head = kids(t)
        if head[0].data in ("fan_head_or", "fan_head_xor"):
            fan_kind = "or" if head[0].data == "fan_head_or" else "xor"
            infos = self.objs(kids(t, "obj_alts")[0])
            process = self.ref([c for c in head if self.is_ref(c)][-1])
            links = [self.procedural("agent", process, i) for i in infos]
            self.fan(fan_kind, links, process)
            return
        if kids(t, "any_of") or kids(t, "one_of"):
            fan_kind = "or" if kids(t, "any_of") else "xor"
            info = self.obj(kids(t, "obj")[0])
            links = [self.procedural("agent", p, info) for p in self.refs(kids(t, "proc_alts")[0])]
            self.fan(fan_kind, links, info["name"])
            return
        process = self.ref([c for c in head if self.is_ref(c)][-1])
        for info in self.objs(kids(t, "obj_list")[0]):
            self.procedural("agent", process, info)

    def change_item(self, process: str, t, modifier: str | None = None):
        link = self.link("ChangeLink", process=self.thing(process, "process"))
        if modifier:
            self.g.add((link, OPM.modifier, Literal(modifier)))
            self.g.add((link, RDF.type, OPM.EventLink if modifier == "event" else OPM.ConditionLink))
        obj = self.ref([c for c in kids(t) if self.is_ref(c)][0])
        self.g.add((link, OPM.object, self.thing(obj, "object")))
        states = [" ".join(toks(c)) for c in kids(t, "state")]
        if t.data in ("change_io", "change_in", "change_in_alts", "opc_change_any") and states:
            if t.data == "change_in_alts":
                for s in self.states_of(kids(t, "states_or")[0]):
                    self.g.add((link, OPM.fromState, self.state(obj, s)))
            elif not (t.data == "opc_change_any" and "any" in toks(t)[:1]):
                self.g.add((link, OPM.fromState, self.state(obj, states[0])))
        if t.data in ("change_out", "opc_change_to_state") and states:
            self.g.add((link, OPM.toState, self.state(obj, states[0])))
        if t.data == "change_in_alts" and states:
            self.g.add((link, OPM.toState, self.state(obj, states[-1])))
        for ts in kids(t, "to_state"):
            for s in self.states_of(ts):
                self.g.add((link, OPM.toState, self.state(obj, s)))
        return link

    def change(self, t):
        head = kids(t)
        process = self.ref(head[0])
        items = [c for c in head[1:] if c.data.startswith("change_") or c.data.startswith("opc_change")]
        links = [self.change_item(process, c) for c in items]
        if kids(t, "any_of") or kids(t, "one_of"):
            self.fan("or" if kids(t, "any_of") else "xor", links, process)

    def event(self, kind: str, t):
        parts = kids(t)
        if kind == "event_agent":
            self.procedural("agent", self.ref(parts[1]), self.obj(parts[0]), "event")
            return
        if kind == "event_agent_fan":                      # Table 23: B initiates and handles exactly one of P, Q
            agent = self.obj(parts[0])
            links = [self.procedural("agent", p, agent, "event") for p in self.refs(kids(t, "proc_alts")[0])]
            self.fan("or" if kids(t, "any_of") else "xor", links, agent["name"])
            return
        initiator = self.obj(parts[0])
        if kind == "event_fan":                            # Tables 22, 23: B initiates exactly one of P, Q, which ...
            self.event_fan(t, initiator)
            return
        process = self.ref(parts[1])
        self.same_object(kind, initiator, parts)
        if kind == "event_consumption":
            self.procedural("consumption", process, initiator, "event")
        elif kind == "event_effect":
            self.procedural("effect", process, initiator, "event")
        elif kind == "event_instrument":
            self.procedural("instrument", process, initiator, "event")
        elif kind in ("event_change", "event_change_any"):
            self.change_item(process, parts[2], "event")

    def event_fan(self, t, initiator: dict):
        """One event link per process of the fan; the tail names the object again ("which consumes Paper") or
        only the process ("which affects the occurring process")."""
        procs = self.refs(kids(t, "proc_alts")[0])
        tail = kids(t, "event_tail")[0]
        verb = toks(tail)[0]
        items = [c for c in kids(tail) if c.data.startswith("change_") or c.data.startswith("opc_change")]
        again = self.obj(kids(tail, "obj")[0])["name"] if kids(tail, "obj") else None
        if items:
            refs = [c for c in kids(items[0]) if self.is_ref(c)]
            again = self.ref(refs[0]) if refs else None
        if again is not None and again != initiator["name"]:
            self.defect("OPM-12-019", f'"{initiator["name"]}" initiates, but "{again}" is the object after "which"')
        if items:
            links = [self.change_item(p, items[0], "event") for p in procs]
        else:
            k = {"consumes": "consumption", "affects": "effect", "requires": "instrument"}[verb]
            links = [self.procedural(k, p, initiator, "event") for p in procs]
        self.fan("or" if kids(t, "any_of") else "xor", links, initiator["name"])

    EVENT_RULE = {"event_consumption": "OPM-9-092", "event_effect": "OPM-9-096", "event_instrument": "OPM-9-102",
                  "event_change": "OPM-9-109", "event_change_any": "OPM-9-115"}

    def same_object(self, kind: str, initiator: dict, parts: list):
        """An event sentence names the initiating object twice (9.5.2): "<Object> initiates <Process>, which consumes
        <Object>." The grammar cannot require the two names (and the initiating and input state) to be equal."""
        rule = self.EVENT_RULE.get(kind)
        if rule is None or len(parts) < 3:
            return
        if kind == "event_consumption" and initiator["state"]:
            rule = "OPM-9-106"
        second = parts[2]
        if second.data == "obj":
            other = self.obj(second)
            name, state = other["name"], other["state"]
        else:                                              # change item: Object from s1 to s2
            refs = [c for c in kids(second) if self.is_ref(c)]
            name = self.ref(refs[0]) if refs else None
            states = [" ".join(toks(c)) for c in kids(second, "state")]
            state = states[0] if states and second.data in ("change_io", "change_in", "change_in_alts") else None
        if name != initiator["name"]:
            self.defect(rule, f'"{initiator["name"]}" initiates, but "{name}" is the object after "which"')
        elif kind == "event_instrument" and initiator["state"] != state:
            self.defect("OPM-9-121", f'the initiating state "{initiator["state"] or "-"}" differs from the required '
                                     f'state "{state or "-"}"')
        elif kind == "event_change" and initiator["state"] and state and initiator["state"] != state:
            self.defect("OPM-9-109" if second.data == "change_io" else "OPM-9-112",
                        f'the initiating state "{initiator["state"]}" is not the input state "{state}"')

    def defect(self, rule: str, detail: str):
        """A wording defect of the sentence itself, found while reading it; validate.py reports it with the
        catalogue entry (opm:Defect with opm:violates = rule id, opm:detail = what is wrong)."""
        node = self.new("d")
        self.g.add((node, RDF.type, OPM.Defect))
        self.g.add((node, OPM.violates, Literal(rule)))
        self.g.add((node, OPM.sentence, self.sentence))
        self.g.add((node, OPM.detail, Literal(detail)))

    def condition(self, kind: str, t):
        parts = kids(t)
        if kind in ("cond_fan", "cond_agent_fan", "cond_requires_fan"):
            self.condition_fan(kind, t)
            return
        wrong = []                                         # wording defects of the sentence itself (9.5.3)
        if kind == "cond_agent":                           # <Agent> handles <Process> if <Agent> ..., else ...
            info, process = self.obj(parts[0]), self.ref(parts[1])
            cond = self.subject(self.subject_node(t))
            if cond["name"] != info["name"]:
                wrong.append(f'"{info["name"]}" handles, but the condition is on "{cond["name"]}"')
            elif info["state"] and cond["state"] and info["state"] != cond["state"]:
                wrong.append(f'the agent state "{info["state"]}" is not the condition state "{cond["state"]}"')
            else:
                info["state"] = info["state"] or cond["state"]     # the condition state is the state of the link
            self.skip_check(wrong, t, process)
            self.procedural("agent", process, info, "condition")
            self.cond_report("agent", {"state": info["state"] or cond["state"]}, None, wrong)
            return
        if kind == "cond_requires_that":
            process, info = self.ref(parts[0]), self.subject(self.subject_node(t))
            self.skip_check(wrong, t, process)
            self.procedural("instrument", process, info, "condition")
            self.cond_report("instrument", info, None, wrong)
            return
        if kind == "opc_cond_negation":
            neg = kids(t, "opc_negation")[0]
            link = self.procedural("instrument", self.ref(parts[0]), self.negated(neg), "condition")
            self.g.add((link, RDF.type, OPM.NegationLink))
            return
        if kind == "ax_cond_there_is":
            process = self.ref(parts[0])
            info = {"name": self.ref(parts[2]), "mult": None, "state": " ".join(toks(parts[1]))}
            effect = kids(t, "cond_effect")[0]
            self.skip_check(wrong, t, process)
            link_kind, item = self.effect_check(wrong, process, info, effect)
            self.cond_effect(process, info, effect)
            self.cond_report(link_kind, info, item, wrong)
            return
        if kind == "cond_if":                              # If <condition> then <...>, otherwise bypass <Process>
            subj = parts[0]
            info = self.obj(subj) if subj.data == "obj" else self.subject(subj)
            then = kids(t, "cond_then")[0]
            words = toks(then)
            refs = [c for c in kids(then) if self.is_ref(c)]
            objs = kids(then, "obj")
            item = None
            if "handles" in words:
                link_kind, process = "agent", self.ref(refs[-1])
                agent = self.obj(objs[0])["name"] if objs else self.ref(refs[0])
                if agent != info["name"]:
                    wrong.append(f'the condition is on "{info["name"]}", but "{agent}" handles')
                self.procedural("agent", process, info, "condition")
            elif "changes" in words:
                link_kind, process = "change", self.ref(refs[0])
                item = [c for c in kids(then) if c.data.startswith("change")][0]
                self.item_check(wrong, info, item)
                self.change_item(process, item, "condition")
            else:
                process = self.ref(refs[0])
                link_kind = "consumption" if "consumes" in words else "effect" if "affects" in words else "instrument"
                if objs and self.obj(objs[0])["name"] != info["name"]:
                    wrong.append(f'the condition is on "{info["name"]}", but "{self.obj(objs[0])["name"]}" is '
                                 f'the object after "{"consumes" if "consumes" in words else "affects"}"')
                self.procedural(link_kind, process, info, "condition")
            bypass = self.ref([c for c in parts if self.is_ref(c)][-1])
            if bypass != process:
                wrong.append(f'"{process}" is the conditioned process, but "{bypass}" is bypassed')
            self.cond_report(link_kind, info, item, wrong)
            return
        # cond_occurs
        process = self.ref(parts[0])
        subj = self.subject_node(t)
        info = self.subject(subj)
        effects = kids(t, "cond_effect")
        self.skip_check(wrong, t, process)
        if effects:
            link_kind, item = self.effect_check(wrong, process, info, effects[0])
            self.cond_effect(process, info, effects[0])
            self.cond_report(link_kind, info, item, wrong)
        else:
            link = self.procedural("instrument", process, info, "condition")
            if kids(subj, "opc_negation"):
                self.g.add((link, RDF.type, OPM.NegationLink))
            self.cond_report("instrument", info, None, wrong)

    # A condition sentence names its object and its process twice, and the condition state is the input state of a
    # change (9.5.3.1.1 to 9.5.3.4.2). The grammar cannot require the names to be equal; as in event sentences
    # (same_object) the converter compares them and reports the entry of the sentence form.

    def skip_check(self, wrong: list, t, process: str):
        """"..., otherwise <Process> is skipped" / "bypass <Process>" names the conditioned process."""
        for sk in kids(t, "skip"):
            refs = [c for c in kids(sk) if self.is_ref(c)]
            if refs and self.ref(refs[0]) != process:
                wrong.append(f'"{process}" is the conditioned process, but "{self.ref(refs[0])}" is skipped')

    def item_check(self, wrong: list, info: dict, item):
        """"changes <Object> from <state> ...": the object and the input state are those of the condition."""
        refs = [c for c in kids(item) if self.is_ref(c)]
        name = self.ref(refs[0]) if refs else None
        states = [" ".join(toks(c)) for c in kids(item, "state")]
        start = states[0] if states and item.data in ("change_io", "change_in") else None
        if name != info["name"]:
            wrong.append(f'the condition is on "{info["name"]}", but "{name}" is the object after "changes"')
        elif info["state"] and start and info["state"] != start:
            wrong.append(f'the condition state "{info["state"]}" is not the input state "{start}"')

    def effect_check(self, wrong: list, process: str, info: dict, effect) -> tuple:
        """The part after "in which case": (kind of the link, change item or None)."""
        words = toks(effect)
        refs = [c for c in kids(effect) if self.is_ref(c)]
        objs = kids(effect, "obj")
        items = [c for c in kids(effect) if c.data.startswith("change") or c.data.startswith("opc_change")]
        if "consumed" in words:                            # <Object> is consumed
            if self.ref(refs[0]) != info["name"]:
                wrong.append(f'the condition is on "{info["name"]}", but "{self.ref(refs[0])}" is consumed')
            return "consumption", None
        verb = next((w for w in words if w in ("consumes", "affects", "changes")), "")
        if refs and self.ref(refs[0]) != process:          # not "the occurring process"
            wrong.append(f'"{process}" is the conditioned process, but "{self.ref(refs[0])}" {verb} the object')
        if items:
            self.item_check(wrong, info, items[0])
            return "change", items[0]
        if objs and self.obj(objs[0])["name"] != info["name"]:
            wrong.append(f'the condition is on "{info["name"]}", but "{self.obj(objs[0])["name"]}" is the object '
                         f'after "{verb}"')
        return ("consumption" if verb == "consumes" else "effect"), None

    def cond_report(self, link_kind: str, info: dict, item, wrong: list, fan: bool = False):
        if not wrong:
            return
        stated = bool(info.get("state"))
        rule = {"consumption": "OPM-9-144" if stated else "OPM-9-132", "effect": "OPM-9-135",
                "agent": "OPM-9-157" if stated else "OPM-9-138",
                "instrument": "OPM-9-160" if stated else "OPM-9-141"}.get(link_kind)
        if link_kind == "change":
            rule = "OPM-9-151" if item.data == "change_in" else "OPM-9-154" if item.data in (
                "change_out", "opc_change_to_state") else "OPM-9-147"
        self.defect("OPM-12-019" if fan else rule, "; ".join(wrong))

    @staticmethod
    def subject_node(t):
        """The condition subject; the OPCloud alternative "is at state" is aliased to its own node name."""
        return next(c for c in kids(t) if c.data in ("cond_subject", "opc_cond_at_state"))

    def subject(self, subj) -> dict:
        if kids(subj, "opc_negation"):
            return self.negated(kids(subj, "opc_negation")[0])
        objs = kids(subj, "obj")
        if objs:
            return self.obj(objs[0])
        refs = [c for c in kids(subj) if self.is_ref(c)]
        states = kids(subj, "state")
        return {"name": self.ref(refs[0]), "mult": None, "state": " ".join(toks(states[0])) if states else None}

    def negated(self, neg) -> dict:
        refs = [c for c in kids(neg) if self.is_ref(c)] or [c for o in kids(neg, "obj") for c in kids(o)
                                                               if self.is_ref(c)]
        states = kids(neg, "state")
        return {"name": self.ref(refs[0]), "mult": None, "state": " ".join(toks(states[0])) if states else None}

    def cond_effect(self, process: str, info: dict, effect):
        words = toks(effect)
        if "consumed" in words or "consumes" in words:
            return self.procedural("consumption", process, info, "condition")
        if "affects" in words:
            return self.procedural("effect", process, info, "condition")
        if "changes" in words:
            item = [c for c in kids(effect) if c.data.startswith("change") or c.data.startswith("opc_change")][0]
            return self.change_item(process, item, "condition")
        return None

    def condition_fan(self, kind: str, t):
        """Condition link fans (12.5, 12.6, Tables 22, 23): one condition link per process of the fan."""
        procs = self.refs(kids(t, "proc_alts")[0])
        wrong = []
        if kind == "cond_agent_fan":
            info = self.obj(kids(t, "obj")[0])
            cond = self.subject(self.subject_node(t))
            if cond["name"] != info["name"]:
                wrong.append(f'"{info["name"]}" handles, but the condition is on "{cond["name"]}"')
            else:
                info["state"] = info["state"] or cond["state"]
            links = [self.procedural("agent", p, info, "condition") for p in procs]
            self.fan("or" if kids(t, "any_of") else "xor", links, info["name"])
            self.cond_report("agent", info, None, wrong, fan=True)
            return
        info = self.subject(self.subject_node(t))
        if kind == "cond_requires_fan":
            links = [self.procedural("instrument", p, info, "condition") for p in procs]
        else:
            effect = kids(t, "cond_effect")[0]
            refs = [c for c in kids(effect) if self.is_ref(c)]
            named = self.ref(refs[0]) if refs and "consumed" not in toks(effect) else None
            link_kind, item = self.effect_check(wrong, named if named in procs else procs[0], info, effect)
            links = [self.cond_effect(p, info, effect) for p in procs]
            self.cond_report(link_kind, info, item, wrong, fan=True)
        self.fan("or" if kids(t, "fan_head_or") else "xor", [l for l in links if l is not None], info["name"])

    # ---------------------------------------------------------------- other sentences

    def sentence_node(self, s: parser.Sentence):
        node = M[f"s{s.line}_{s.column}"]
        self.g.add((node, RDF.type, OPM.Sentence))
        self.g.add((node, OPM.line, Literal(s.line)))
        self.g.add((node, OPM.column, Literal(s.column)))
        self.g.add((node, OPM.text, Literal(s.text)))
        for d in sorted(s.dialect):
            self.g.add((node, OPM.dialect, Literal(d)))
        if self.opd is not None:
            self.g.add((node, OPM.inOPD, self.opd))
        return node

    def opd_node(self, label: str | None):
        if label is None:
            return None
        node = M["opd_" + slug(label)]
        self.g.add((node, RDF.type, OPM.OPD))
        self.g.add((node, OPM.label, Literal(label)))
        return node

    def add(self, s: parser.Sentence):
        self.opd = self.opd_node(s.opd)
        self.sentence = self.sentence_node(s)
        self.path = None
        node = s.tree.children[0]
        if node.data == "path_sentence":
            self.path = str(node.children[0])
            node = kids(node)[0]
        self.start = getattr(node.meta, "start_pos", None)
        self.dispatch(node)

    def dispatch(self, t):
        d = t.data
        if d in ("consumption", "result", "effect", "instrument"):
            self.transforming(d, t)
        elif d == "agent":
            self.agent(t)
        elif d == "fig_agent_procs":                       # Figure 47: one link per agent and process
            for info in self.objs(kids(t, "obj_list")[0]):
                for process in self.refs(t):
                    self.procedural("agent", process, info)
        elif d == "change":
            self.change(t)
        elif d.startswith("event_"):
            self.event(d, t)
        elif d.startswith("cond_") or d in ("ax_cond_there_is", "opc_cond_negation"):
            self.condition(d, t)
        elif d == "invocation":
            self.invocation(t)
        elif d == "exception":
            self.exception(t)
        elif d == "opc_duration":
            self.duration(t)
        elif d == "probabilistic":
            self.probabilistic(t)
        elif d == "generic_property":
            self.generic(t)
        elif d in ("state_enum", "state_enum_open", "single_state", "state_mark", "states_mark",
                   "ax_combined_state", "opc_stateful", "state_sentence"):
            self.states_sentence(d, t)
        elif d == "aggregation":
            self.aggregation(t)
        elif d in ("exhibition", "state_characterization"):
            self.exhibition(d, t)
        elif d in ("attribute_value", "attribute_range", "attribute_range_clause"):
            self.attribute(d, t)
        elif d.startswith("gen_") or d.startswith("ax_gen"):
            self.generalization(d, t)
        elif d in ("instantiation", "ax_instances"):
            self.instantiation(t)
        elif d in ("tagged_null", "reciprocal_null", "opc_reciprocal_null", "reciprocal", "tagged_uni"):
            self.tagged(d, t)
        elif d in ("zoom", "ax_zoom_new_diagram"):
            self.zoom(t)
        elif d in ("unfold", "unfold_new_diagram"):
            self.unfold(d, t)
        elif d == "refinement":
            self.refinement(t)

    def invocation(self, t):
        head = kids(t)
        if head[0].data in ("fan_head_or", "fan_head_xor"):
            procs = self.refs(kids(t, "proc_alts")[0])
            target = self.ref([c for c in head if self.is_ref(c)][-1])
            links = [self.link("InvocationLink", source=self.thing(p, "process"),
                               target=self.thing(target, "process")) for p in procs]
            self.fan("or" if head[0].data == "fan_head_or" else "xor", links, target)
            return
        source = self.ref(head[0])
        if not kids(t, "proc_list") and not kids(t, "proc_alts"):                  # invokes itself
            self.link("InvocationLink", source=self.thing(source, "process"), target=self.thing(source, "process"))
            return
        targets = self.refs((kids(t, "proc_list") or kids(t, "proc_alts"))[0])
        if source in targets and not kids(t, "any_of") and not kids(t, "one_of"):
            self.defect("OPM-9-129", f'"{source}" is named again instead of "itself"')
        links = [self.link("InvocationLink", source=self.thing(source, "process"), target=self.thing(x, "process"))
                 for x in targets]
        if kids(t, "any_of") or kids(t, "one_of"):
            self.fan("or" if kids(t, "any_of") else "xor", links, source)

    def exception(self, t):
        inner = kids(t, "opc_exception")[0] if kids(t, "opc_exception") else t
        words = toks(inner)
        refs = [c for c in kids(inner) if self.is_ref(c)]
        handler, source = self.ref(refs[0]), self.ref(refs[1])
        dur = " ".join(toks(kids(inner, "duration")[0]))
        link = self.link("ExceptionLink", source=self.thing(source, "process"), target=self.thing(handler, "process"))
        self.g.add((link, OPM.exceptionKind, Literal("undertime" if "short" in words else "overtime")))
        self.g.add((link, OPM.duration, Literal(dur)))

    def duration(self, t):
        words = toks(t)
        process = self.ref([c for c in kids(t) if self.is_ref(c)][0])
        durs = [" ".join(toks(c)) for c in kids(t, "duration")]
        node = self.thing(process, "process")
        self.g.add((node, OPM.durationSentence, self.sentence))
        if "minimal" in words and "maximal" in words and len(durs) == 2:
            self.g.add((node, OPM.minDuration, Literal(durs[0])))
            self.g.add((node, OPM.maxDuration, Literal(durs[1])))
        elif "minimal" in words:
            self.g.add((node, OPM.minDuration, Literal(durs[0])))
        elif "maximal" in words:
            self.g.add((node, OPM.maxDuration, Literal(durs[0])))

    def probabilistic(self, t):
        items = [c for c in kids(t) if c.data in ("prob_item", "prob_bare")]   # a bare member has no probability
        links = []
        if items:
            process = self.ref(kids(t)[0])
            for it in items:
                link = self.procedural("result", process, self.obj(kids(it, "obj")[0]))
                if it.data == "prob_item":
                    self.g.add((link, OPM.probability, Literal(str(it.children[-1]))))
                links.append(link)
            self.fan("probabilistic", links, process)
            return
        procs = [c for c in kids(t) if c.data in ("prob_proc", "prob_proc_bare")]
        info = self.obj(kids(t, "obj")[0])
        verb = {"consumes": "consumption", "yields": "result", "affects": "effect"}.get(
            next((w for w in toks(t) if w in ("consumes", "yields", "affects")), "consumes"), "consumption")
        for pp in procs:
            link = self.procedural(verb, self.ref(kids(pp)[0]), info)
            if pp.data == "prob_proc":
                self.g.add((link, OPM.probability, Literal(str(pp.children[-1]))))
            links.append(link)
        self.fan("probabilistic", links, info["name"])

    def generic(self, t):
        name = self.ref(kids(t)[0])
        node = self.thing(name)
        self.g.add((node, OPM.propertySentence, self.sentence))
        kind = kids(t, "kind")
        if kind:
            k = toks(kind[0])[0] if toks(kind[0]) else None
        for p in kids(t, "prop"):
            v = toks(p)[0].lower()
            prop = "essence" if v in ("physical", "informatical") else "affiliation" \
                if v in ("systemic", "environmental") else "perseverance"
            if prop != "perseverance":
                self.g.add((node, OPM[prop + "Stated"], Literal(v)))
                self.stated[name][prop] = v
        for k in kids(t, "kind"):
            word = " ".join(str(c) for c in k.children) if k.children else ""
            if word:
                self.kind[name].add(word)
                self.g.add((node, OPM.declaredKind, Literal(word)))

    def states_sentence(self, d, t):
        if d == "state_sentence" and kids(t, "opc_state_mark"):     # OPCloud "State s is initial."
            t, d = kids(t, "opc_state_mark")[0], "opc_state_mark"
        refs = [c for c in kids(t) if self.is_ref(c)]
        owner = self.ref(refs[0]) if refs else None
        words = toks(t)
        if d in ("state_enum", "state_enum_open"):
            for s in self.states_of(t):
                self.state(owner, s, declared=True)
            if d == "state_enum_open":
                self.g.add((self.thing(owner, "object"), OPM.statesSuppressed, Literal(True)))
        elif d == "single_state":
            self.state(owner, self.states_of(t)[0], declared=True)
        elif d in ("state_mark", "states_mark", "opc_state_mark"):
            marks = [" ".join(toks(m)) for m in t.iter_subtrees() if m.data == "marks"]
            marks = " ".join(marks).split() or [w for w in words if w in ("initial", "final", "default")]
            if owner is None:                          # OPCloud "State s is initial." -> object of the last enum
                owner = self.last_owner
            if owner is None:                          # no object to attach the state to
                self.defect("OPC-003", "no state sentence of an object stands before it, so the state has no "
                                       "object")
                return
            for s in self.states_of(t):
                node = self.state(owner, s)
                for mk in marks:
                    if mk in ("initial", "final", "default"):
                        self.g.add((node, OPM["is" + mk.capitalize()], Literal(True)))
                        self.g.add((node, OPM.markSentence, self.sentence))
        elif d == "ax_combined_state":
            sts = self.states_of(t)
            for st, mark in ((sts[0], OPM.isInitial), (sts[-1], OPM.isFinal)):
                self.g.add((self.state(owner, st, True), mark, Literal(True)))
                self.g.add((self.state(owner, st), OPM.markSentence, self.sentence))
        elif d == "opc_stateful":
            self.thing(owner, "object")
        if owner:
            self.last_owner = owner

    last_owner = None

    def aggregation(self, t):
        whole = self.ref(kids(t)[0])
        plist = kids(t, "part_list")[0]
        for info in self.objs(plist):
            link = self.link("AggregationLink", source=self.thing(whole), target=self.thing(info["name"]))
            if info["mult"]:
                self.g.add((link, OPM.multiplicity, Literal(info["mult"])))
                self.ranges(info["mult"])
            if info["where"]:
                self.g.add((link, OPM.constraint, Literal(info["where"])))
        words = toks(plist)
        if "other" in words:
            self.g.add((self.thing(whole), OPM.incomplete, Literal(True)))

    def exhibition(self, d, t):
        exhibitor = self.ref(kids(t)[0])
        if d == "state_characterization":
            value = " ".join(toks(kids(t, "state")[0]))
            attr = self.ref([c for c in kids(t) if self.is_ref(c)][1]) + " of " + exhibitor
            link = self.link("ExhibitionLink", source=self.thing(exhibitor), target=self.thing(attr, "object"))
            self.g.add((link, OPM.targetState, self.state(attr, value)))
            self.g.add((self.thing(attr), OPM.value, Literal(value)))      # fixes the value, as "Attr of X is v"
            return
        for n, fl in enumerate(kids(t, "feat_list")):
            for info in self.objs(fl):
                feature = info["name"] + " of " + exhibitor
                link = self.link("ExhibitionLink", source=self.thing(exhibitor), target=self.thing(feature))
                self.g.add((link, OPM.featureList, Literal(n + 1)))
            if "other" in toks(fl) or kids(fl, "partial_feature"):
                self.g.add((self.thing(exhibitor), OPM.incomplete, Literal(True)))

    def attribute(self, d, t):
        feat = self.ref(kids(t)[0])
        node = self.thing(feat, "object")
        values = [" ".join(str(x) for x in v.scan_values(lambda x: isinstance(x, Token))) for v in kids(t, "value")]
        if d == "attribute_value":
            self.g.add((node, OPM.value, Literal(values[0])))
            self.state(feat, values[0])
            self.values.append((feat, values[0], self.sentence))      # declared or checked in finish()
        else:
            self.g.add((node, OPM.rangeMin, Literal(values[0])))
            self.g.add((node, OPM.rangeMax, Literal(values[-1])))

    def generalization(self, d, t):
        if d in ("gen_many",):
            general = self.singular(self.ref(kids(t)[-1]))
            specials = self.refs(kids(t, "proc_list")[0])
        elif d in ("gen_single", "gen_single_process"):
            r = [c for c in kids(t) if self.is_ref(c)]
            specials, general = [self.ref(r[0])], self.ref(r[-1])
        else:
            r = [self.ref(c) for c in kids(t) if self.is_ref(c)]
            general, specials = r[-1], r[:-1]
            if d == "gen_partial":
                general = self.singular(general)
                self.g.add((self.thing(general), OPM.incomplete, Literal(True)))
        for sp in specials:
            self.link("GeneralizationLink", source=self.thing(general), target=self.thing(sp))

    def instantiation(self, t):
        r = [c for c in kids(t) if self.is_ref(c)]
        cls = self.ref(r[-1])
        instances = [self.ref(r[0])] if len(r) == 2 else self.refs(kids(t, "proc_list")[0])
        for i in instances:
            self.link("ClassificationLink", source=self.thing(cls), target=self.thing(i))

    def tagged(self, d, t):
        o = [self.obj(c) for c in kids(t, "obj")]
        tag = {"tagged_null": "relates to", "reciprocal_null": "related", "opc_reciprocal_null": "equivalent"}.get(d)
        if tag is None:
            tag = " ".join(str(c) for c in t.children if isinstance(c, Token))
        link = self.link("TaggedLink", source=self.thing(o[0]["name"]), target=self.thing(o[1]["name"]))
        self.g.add((link, OPM.tag, Literal(tag)))
        if d in ("reciprocal_null", "opc_reciprocal_null", "reciprocal"):
            self.g.add((link, OPM.reciprocal, Literal(True)))
        for end, info in (("source", o[0]), ("target", o[1])):
            if info["state"]:
                self.g.add((link, OPM[end + "State"], self.state(info["name"], info["state"])))
            if info["mult"]:
                self.g.add((link, OPM[end + "Multiplicity"], Literal(info["mult"])))
                self.ranges(info["mult"])
            if info["where"]:
                self.g.add((link, OPM.constraint, Literal(info["where"])))

    def zoom(self, t):
        inner = kids(t, "opc_zoom")[0] if kids(t, "opc_zoom") else t
        refs = [c for c in kids(inner) if self.is_ref(c)]
        process = self.ref(refs[0])
        opds = [str(c) for c in inner.children if isinstance(c, Token) and re.fullmatch(r"SD\d*(?:\.\d+)*", str(c))]
        steps = [[self.ref(r) for r in kids(item) if self.is_ref(r)] for item in kids(kids(inner, "seq_list")[0])]
        interim = [i["name"] for ol in kids(inner, "obj_list") for i in self.objs(ol)]
        node = self.new("z")
        self.g.add((node, RDF.type, OPM.InZoom))
        self.g.add((node, OPM.refines, self.thing(process)))
        self.g.add((node, OPM.sentence, self.sentence))
        child = self.opd_node(opds[1]) if len(opds) == 2 else self.opd
        if child is not None:
            self.g.add((node, OPM.childOPD, child))
            self.g.add((child, OPM.refines, self.thing(process)))
            if len(opds) == 2:
                self.g.add((child, OPM.parentOPD, self.opd_node(opds[0])))
                self.refinement_node(self.opd_node(opds[0]), child, self.thing(process), "in-zooming")
        self.shown(child, [sub for group in steps for sub in group] + interim)
        self.zooms.append((process, steps, interim, node))

    def shown(self, opd, names: list):
        """The refinees named by a zoom or unfold sentence appear in the OPD of the refinement (the child OPD of a
        new-diagram form, else the paragraph of the sentence), also when no other sentence there names them."""
        here, self.opd = self.opd, opd
        for name in names:
            self.thing(name)
        self.opd = here

    def refinement_node(self, parent, child, thing, kind: str):
        """One edge of the OPD tree (14.2.2.6.1.1) with the sentence that states it: opm:Refinement."""
        node = self.new("e")
        self.g.add((node, RDF.type, OPM.Refinement))
        self.g.add((node, OPM.sentence, self.sentence))
        self.g.add((node, OPM.parent, parent))
        self.g.add((node, OPM.child, child))
        self.g.add((node, OPM.refines, thing))
        self.g.add((node, OPM.kind, Literal(kind)))

    def unfold(self, d, t):
        refs = [c for c in kids(t) if self.is_ref(c)]
        thing = self.ref(refs[0])
        opds = [str(c) for c in t.children if isinstance(c, Token) and re.fullmatch(r"SD\d*(?:\.\d+)*", str(c))]
        node = self.new("u")
        self.g.add((node, RDF.type, OPM.Unfold))
        self.g.add((node, OPM.refines, self.thing(thing)))
        self.g.add((node, OPM.sentence, self.sentence))
        child = self.opd
        if len(opds) == 2:
            child = self.opd_node(opds[1])
            self.g.add((child, OPM.parentOPD, self.opd_node(opds[0])))
            self.g.add((child, OPM.refines, self.thing(thing)))
            self.refinement_node(self.opd_node(opds[0]), child, self.thing(thing), "unfolding")
        refinees = [r for pl in kids(t, "proc_list") for r in self.refs(pl)]
        self.shown(child, refinees)
        for r in refinees:
            self.g.add((node, OPM.refinee, self.things[r]))
            self.g.add((self.things[thing], OPM.refinee, self.things[r]))       # refinement tree (14-020)

    def refinement(self, t):
        """ "SD is refined by in-zooming P in SD1." names P; it does not place P in the paragraph it stands in
        (the sentence may be written in SD or in SD1): whether P appears in the parent OPD is what OPM-14-094
        checks."""
        opds = [str(c) for c in t.children if isinstance(c, Token) and re.fullmatch(r"SD\d*(?:\.\d+)*", str(c))]
        thing = self.ref([c for c in kids(t) if self.is_ref(c)][0])
        child = self.opd_node(opds[1])
        here, self.opd = self.opd, None
        node = self.thing(thing)
        self.opd = here
        self.g.add((child, OPM.parentOPD, self.opd_node(opds[0])))
        self.g.add((child, OPM.refines, node))
        self.refinement_node(self.opd_node(opds[0]), child, node,
                             "unfolding" if "unfolding" in toks(t) else "in-zooming")

    # ---------------------------------------------------------------- whole model

    def finish(self):
        g = self.g
        self.opd = None                                    # what is added here belongs to no single paragraph
        for name, node in self.things.items():
            for k in self.kind[name]:
                g.add((node, RDF.type, OPM.Object if k == "object" else OPM.Process))
                if (name, k) in self.kind_at:
                    g.add((node, OPM[k + "Sentence"], self.kind_at[(name, k)]))
        # processes in in-zooms and their order (14.2.1.3)
        for process, steps, interim, node in self.zooms:
            pnode = self.things[process]
            if self.kind[process] == {"process"} or not self.kind[process]:
                g.add((pnode, RDF.type, OPM.Process))
            for order, group in enumerate(steps, 1):
                for sub in group:
                    snode = self.thing(sub)
                    g.add((snode, OPM.subprocessOf, pnode))
                    g.add((snode, OPM.stepOrder, Literal(order)))
                    if self.kind[process] != {"object"}:
                        g.add((snode, RDF.type, OPM.Process))
                        if g.value(snode, OPM.processSentence) is None:
                            g.add((snode, OPM.processSentence, g.value(node, OPM.sentence)))
            for obj in interim:
                g.add((self.thing(obj), OPM.interimOf, pnode))
            # links attached to the contour of the in-zoomed process apply to its subprocesses (14.2.2.4.1):
            # those written in the OPD of the in-zoom; without OPD paragraphs every link of the process may be one
            child = g.value(node, OPM.childOPD)
            subs = [self.thing(sub) for group in steps for sub in group]
            for link in list(g.subjects(OPM.process, pnode)):
                opd = g.value(link, OPM.inOPD)
                if (link, RDF.type, OPM.ProceduralLink) in g and (child is None or opd is None or opd == child):
                    for snode in subs:
                        g.add((link, OPM.distributedTo, snode))
        # default generic properties (7.3.4); primary essence = setting, else the essence of the majority of all
        # things: a thing without a stated essence is informatical (the default of Annex A, A.4.4.2), so one
        # sentence "X is physical." among unstated things does not make the whole model physical
        essences = Counter(self.stated[name].get("essence", "informatical") for name in self.things)
        primary = self.primary_essence or ("physical" if essences["physical"] > essences["informatical"]
                                           else "informatical")
        for name, node in self.things.items():
            g.add((node, OPM.essence, Literal(self.stated[name].get("essence", primary))))
            g.add((node, OPM.affiliation, Literal(self.stated[name].get("affiliation", "systemic"))))
        g.add((M.model, RDF.type, OPM.Model))
        g.add((M.model, OPM.primaryEssence, Literal(primary)))
        # states and modifier of each procedural link as one value (OPM-8-004: same kind, other spec = other link)
        for link in list(g.subjects(RDF.type, OPM.ProceduralLink)):
            spec =[f"{p}={','.join(sorted(str(o) for o in g.objects(link, OPM[p])))}"
                    for p in ("state", "fromState", "toState", "modifier")]
            g.add((link, OPM.linkSpec, Literal(";".join(spec))))
        # direct role properties object -> process
        for cls, prop in ROLE.items():
            for link in g.subjects(RDF.type, OPM[cls]):
                o, p = g.value(link, OPM.object), g.value(link, OPM.process)
                if o is not None and p is not None:
                    g.add((o, OPM[prop], p))
                    g.add((o, OPM.transformedBy if prop.endswith("By") else OPM.enables, p))
        # direct structural properties for paths in the shapes: refinement tree, specialization, instances
        for cls, prop in (("AggregationLink", "hasPart"), ("ExhibitionLink", "hasFeature"),
                          ("GeneralizationLink", "hasSpecialization"), ("ClassificationLink", "hasInstance")):
            for link in g.subjects(RDF.type, OPM[cls]):
                s, t = g.value(link, OPM.source), g.value(link, OPM.target)
                if s is not None and t is not None:
                    g.add((s, OPM[prop], t))
                    g.add((s, OPM.refinee, t))
                    if prop == "hasSpecialization":
                        g.add((t, OPM.specializes, s))
                    if prop == "hasInstance":
                        g.add((t, OPM.instanceOf, s))
        for sub, parent in list(g.subject_objects(OPM.subprocessOf)):
            g.add((parent, OPM.refinee, sub))
        # "Attribute of X is v." (11.3) declares v as a possible value of the attribute, unless X is a
        # specialization or an instance whose general or class gives the attribute its possible values (10.3.4.3,
        # 10.3.5.1): then the sentence uses a value, and OPM-9-036 checks it against the inherited ones (A-013)
        valued = {feat for feat, _, _ in self.values} | {owner for (owner, _), st in self.states.items()
                                                         if (st, OPM.declared, Literal(True)) in g}
        for feat, value, sentence in self.values:
            fname, owner = feat.split(" of ", 1)
            todo, seen, inherits = [self.things[owner]], set(), False
            while todo and not inherits:
                thing = todo.pop()
                for anc in [a for p in (OPM.specializes, OPM.instanceOf) for a in g.objects(thing, p)]:
                    if anc not in seen:
                        seen.add(anc)
                        todo.append(anc)
                        inherits = inherits or f"{fname} of {g.value(anc, OPM.name)}" in valued
            if inherits:
                g.add((self.states[(feat, value)], OPM.valueSentence, sentence))
            else:
                g.add((self.states[(feat, value)], OPM.declared, Literal(True)))
        for name, node in self.things.items():
            if " of " in name:
                g.add((node, OPM.featureName, Literal(name.split(" of ", 1)[0])))
        # exhibition: attribute or operation by list position and kind of the exhibitor (10.3.3.1)
        for link in g.subjects(RDF.type, OPM.ExhibitionLink):
            n = g.value(link, OPM.featureList)
            if n is None:
                continue
            exhibitor = g.value(link, OPM.source)
            is_process = (exhibitor, RDF.type, OPM.Process) in g
            first = "operation" if is_process else "attribute"
            other = "attribute" if is_process else "operation"
            g.add((link, OPM.featureRole, Literal(first if int(n) == 1 else other)))
        # minimal and maximal duration of a process as numbers, when both are given in the same unit (OPM-9-170):
        # from the duration sentences of OPCloud and from the exception links (9.5.4.2, 9.5.4.3)
        for node in list(self.things.values()):
            mins = [(str(d), g.value(node, OPM.durationSentence)) for d in g.objects(node, OPM.minDuration)]
            maxs = [(str(d), g.value(node, OPM.durationSentence)) for d in g.objects(node, OPM.maxDuration)]
            for link in g.subjects(OPM.source, node):
                if (link, RDF.type, OPM.ExceptionLink) in g:
                    under = str(g.value(link, OPM.exceptionKind)) == "undertime"
                    (mins if under else maxs).append((str(g.value(link, OPM.duration)), g.value(link, OPM.sentence)))
            for dmin, smin in mins:
                for dmax, smax in maxs:
                    low, high = amount(dmin), amount(dmax)
                    if low and high and low[1] == high[1]:
                        g.add((node, OPM.minDurationValue, Literal(low[0])))
                        g.add((node, OPM.maxDurationValue, Literal(high[0])))
                        if low[0] > high[0]:               # for the report: the two durations as written, where
                            g.add((node, OPM.durationsShown, Literal(f"{dmin} | {dmax}")))
                            g.add((node, OPM.durationsSentence, max(smin, smax, key=lambda s: (
                                int(g.value(s, OPM.line)), int(g.value(s, OPM.column))))))
        # humans: only when the input gives the list
        if self.humans is not None:
            for name, node in self.things.items():
                if (node, RDF.type, OPM.Object) in g:
                    g.add((node, OPM.isHuman, Literal(name.lower() in self.humans)))
        return g


DECLARING = ("state_enum", "state_enum_open", "single_state", "state_mark", "states_mark", "ax_combined_state")


def scan(b: Builder, doc: parser.Document):
    """Whole-model facts needed before reading a sentence: declared states and the names used other than as the
    object at the start of a sentence (for a capitalised initial state, Builder.initial_state)."""
    for s in doc.sentences:
        if not s.ok:
            continue
        node = s.tree.children[0]
        if node.data == "path_sentence":
            node = kids(node)[0]
        start = getattr(node.meta, "start_pos", None)
        if node.data in DECLARING:
            refs = [c for c in kids(node) if b.is_ref(c)]
            if refs:
                b.declared.setdefault(b.ref(refs[0]), set()).update(b.states_of(node))
        skip = set()
        for t in node.iter_subtrees_topdown():
            if b.is_ref(t):
                b.names.add(b.ref(t))
            if t.data == "obj" and getattr(t.meta, "start_pos", None) == start:
                skip.update(id(c) for c in kids(t) if b.is_ref(c))
            elif b.is_ref(t) and id(t) not in skip:
                b.named_elsewhere.add(b.ref(t))


def convert(doc: parser.Document, humans: set | None = None, primary_essence: str | None = None) -> Graph:
    b = Builder({h.strip().lower() for h in humans} if humans is not None else None, primary_essence)
    scan(b, doc)
    for label, title, line in doc.paragraphs:         # paragraph heading = where an OPD rule is reported
        node = M[f"p{line}"]
        b.g.add((node, RDF.type, OPM.Paragraph))
        b.g.add((node, OPM.heads, b.opd_node(label)))
        b.g.add((node, OPM.line, Literal(line)))
        b.g.add((node, OPM.column, Literal(1)))
        b.g.add((node, OPM.text, Literal(f"{label}: {title}" if title else label)))
    for s in doc.sentences:
        if s.ok:
            try:
                b.add(s)
            except Exception as ex:  # noqa: BLE001  a defect of the converter must not stop the validation
                b.g.add((b.sentence, OPM.unconverted, Literal(f"{type(ex).__name__}: {ex}")))
    return b.finish()


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    doc = parser.read(Path(argv[0]).read_text(encoding="utf-8"))
    humans = None
    if "--humans" in argv:
        humans = set(argv[argv.index("--humans") + 1].split(","))
    g = convert(doc, humans)
    if "--turtle" in argv:
        g.serialize(destination=argv[argv.index("--turtle") + 1], format="turtle")
    links = len(set(g.subjects(RDF.type, OPM.Link)))
    print(f"vět {len(doc.sentences)}, nečitelných {sum(not s.ok for s in doc.sentences)}, trojic {len(g)}, "
          f"věcí {len(set(g.subjects(RDF.type, OPM.Thing)))}, vazeb {links}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
