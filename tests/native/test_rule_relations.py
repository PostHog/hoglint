"""Relations between sibling rules, judged by the native evaluator alone.

A generated rule is judged next to rules derived from it: its selection negated, split into its
parts, reordered, a predicate moved into the selection, an `unless` taken out, one identity column
more. The rows of the siblings must stand in a fixed relation, stated over the three outcomes
(violation, clean, unknown or unresolved). So an unknown read as a definite answer in one sibling
and not in another breaks a relation, with no second engine to judge any row."""

from __future__ import annotations

import random
from array import array
from collections import Counter
from collections.abc import Callable, Iterable
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from hoglint.config import Config, ConfigError
from hoglint.model import Context, Resolution
from hoglint.native.rules import NativeRuleContext
from hoglint.rules.engine import Engine, primitive_of
from hoglint.selectors import And, Bare, Compare, ListLiteral, Literal, Node, Not, Or, Parser, Ref, leaves

from native.require import native_module
from native.test_evaluator import CONFIG, REFS, UNKNOWN_TYPES, Tables, Varied, World, render_toml, rule_table

CONNECTIVES = Path(__file__).parents[1] / "rules" / "connectives.txt"

# The rule types the evaluator judges per destination (`NATIVE_MODE == "judge"`).
JUDGED = ["edge", "entrypoint", "signature", "use-shape", "inventory-classify"]
PREDICATE_KEYS = ("deny", "allow", "require")
# Keys of a generated table that change no judgment; siblings drop them so each loads alone.
UNJUDGED_KEYS = ("growth_key", "doc", "remedy", "report", "counted")

# A destination's outcome under one rule: its judgment, or "clean" where the rule gives no row.
CLEAN = "clean"
VIOLATION = "violation"
UNKNOWN_SELECTION = "unknown-selection"
UNKNOWN_VIOLATION = "unknown-violation"
UNRESOLVED = "unresolved"

# Generated families: rounds of each judged type over the fixture's config, then configs that
# differ outside `[rules]`, and export rules.
ROUNDS = 60
VARIED = 100
EXPORTS = 80
# Per relation, the families whose siblings must give some row, a floor below what the seeds give.
TELLING = {"R0": 150, "R1": 200, "R2": 40, "R3": 40, "R4": 25, "R5": 150, "R6": 300, "R8": 2, "R9": 50}

# A type no destination names.
NOTHING = "nowhere.Nothing"
# Leaf truths R0 must read as unknown on a resolved destination, a floor below what the seeds give.
UNKNOWN_LEAVES = 500

Key = tuple[int, int]
Rows = dict[Key, tuple[str, tuple[str, ...]]]


class Connectives:
    """`tests/rules/connectives.txt`: the truth of `and`, `or` and `not` over true, false and
    unknown. The evaluator's Rust unit test reads the same table."""

    def __init__(self, path: Path) -> None:
        self.table: dict[tuple[str, str, str], str] = {}
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            connective, left, right, truth = line.split()
            self.table[(connective, left, right)] = truth

    def join(self, connective: str, parts: Iterable[str]) -> str:
        parts = list(parts)
        out = parts[0]
        for part in parts[1:]:
            out = self.table[(connective, out, part)]
        return out

    def negate(self, truth: str) -> str:
        return self.table[("not", truth, "-")]

    def truth(self, node: Node, leaf: Callable[[Node], str]) -> str:
        """The truth of `node` from the truth of each of its leaves."""
        if isinstance(node, And):
            return self.join("and", (self.truth(part, leaf) for part in node.parts))
        if isinstance(node, Or):
            return self.join("or", (self.truth(part, leaf) for part in node.parts))
        if isinstance(node, Not):
            return self.negate(self.truth(node.inner, leaf))
        return leaf(node)


def parse(text: str) -> Node:
    return Parser(text).parse()


def operand_text(operand: Ref | Literal | ListLiteral) -> str:
    if isinstance(operand, Ref):
        return str(operand)
    if isinstance(operand, ListLiteral):
        return "[" + ", ".join(operand_text(Literal(item)) for item in operand.items) + "]"
    value = operand.value
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return f'"{value}"' if "'" in value else f"'{value}'"


def text(node: Node) -> str:
    """A selector's source text; every part in parentheses."""
    if isinstance(node, And):
        return " and ".join(f"({text(part)})" for part in node.parts)
    if isinstance(node, Or):
        return " or ".join(f"({text(part)})" for part in node.parts)
    if isinstance(node, Not):
        return f"not ({text(node.inner)})"
    if isinstance(node, Bare):
        return operand_text(node.operand)
    return f"{operand_text(node.left)} {node.op} {operand_text(node.right)}"


def expand_membership(node: Node) -> Node:
    """`node` with each `x in [a, b]` written as `x == a or x == b`, and `not in` as its `not`."""
    if isinstance(node, (And, Or)):
        return type(node)(tuple(expand_membership(part) for part in node.parts))
    if isinstance(node, Not):
        return Not(expand_membership(node.inner))
    if not isinstance(node, Compare) or node.op not in ("in", "not in"):
        return node
    if not isinstance(node.right, ListLiteral) or isinstance(node.left, ListLiteral) or not node.right.items:
        return node
    equal = [Compare("==", node.left, Literal(item)) for item in node.right.items]
    either: Node = equal[0] if len(equal) == 1 else Or(tuple(equal))
    return either if node.op == "in" else Not(either)


def conjunction(*texts: str) -> str:
    return " and ".join(f"({part})" for part in texts if part)


@cache
def no_place() -> int:
    return native_module().NO_PLACE


class Judge:
    """One config's rules judged by the native evaluator: per rule, each destination it does
    not judge clean, as (edge id, destination) -> (judgment, identity)."""

    def __init__(self, world: World, rules: str, base: str) -> None:
        self.config: Config = world.config(rules, base)
        labeler = Engine(self.config, world.graph, world.repo).labeler
        self.ctx = NativeRuleContext(self.config, world.graph, labeler, world.repo)
        self.ctx.find_conflicts()

    def rows(self, name: str) -> Rows:
        """`Rules.judge`'s columns read as `judged_rows` reads them, without its check of the
        native module's sources on every call, which costs more than the judging here."""
        columns = self.ctx.evaluator.judge(self.ctx.plan.index[name])
        names: list[str] = columns[2]
        identities: list[list[str]] = columns[4]
        edges, targets, judgment_at, identity_at = (array("I", columns[at]) for at in (0, 1, 3, 5))
        out: Rows = {}
        for at in range(len(edges)):
            key = (edges[at], targets[at])
            assert key not in out, f"{name}: two rows for one destination"
            identity = identities[identity_at[at]] if identity_at[at] != no_place() else []
            out[key] = (names[judgment_at[at]], tuple(identity))
        return out

    def rule(self, name: str) -> Any:
        spec = self.config.rules[name]
        return primitive_of(spec)(self.ctx, spec)


def outcome(rows: Rows, key: Key) -> str:
    return rows[key][0] if key in rows else CLEAN


class Seen:
    """What a loaded rule sees, as its type's defaults fill it in: its kinds, contexts and
    endpoint. An edge rule given these keys sees the same destinations."""

    def __init__(self, judge: Judge, name: str) -> None:
        rule = judge.rule(name)
        self.kinds = sorted(set(rule.kinds()))
        self.include = sorted(rule.include)
        self.exclude = sorted(rule.exclude)
        self.endpoint = judge.config.rules[name].endpoint

    def keys(self) -> dict[str, Any]:
        contexts = {"include": self.include} if self.include else {}
        contexts |= {"exclude": self.exclude} if self.exclude or not self.include else {}
        out: dict[str, Any] = {"contexts": contexts, "endpoint": self.endpoint}
        return out | ({"kinds": self.kinds} if self.kinds else {})


class Family:
    """A generated rule table and the siblings derived from it."""

    def __init__(self, mode: str, table: dict[str, Any], base: str, seen: Seen) -> None:
        self.mode = mode
        self.seen = seen
        self.table = {key: value for key, value in table.items() if key not in UNJUDGED_KEYS}
        self.table["ledger"] = False
        self.base = base
        self.select = self.table.get("select", "")
        self.start = self.table.get("from", "")
        self.predicates = {key: self.table[key] for key in PREDICATE_KEYS if key in self.table}
        self.unless = self.table.get("unless", "")
        # Every selector of the rule, each read as an edge rule's selection.
        keys = ("select", "from", *PREDICATE_KEYS, "unless")
        self.selections = list(dict.fromkeys(self.table[key] for key in keys if key in self.table))

    def edge_core(self, keep_from: bool = True) -> dict[str, Any]:
        """An edge rule over the destinations this rule sees, with no selection and no
        predicate: each destination it sees is a row."""
        core: dict[str, Any] = {"primitive": "edge", "ledger": False, **self.seen.keys()}
        for key in ("flow", "resolve_calls"):
            if key in self.table:
                core[key] = self.table[key]
        if keep_from and self.start:
            core["from"] = self.start
        return core

    def without(self, *keys: str) -> dict[str, Any]:
        return {key: value for key, value in self.table.items() if key not in keys}


class Relations:
    """The relations, each over one family; each returns what broke, and counts the families it
    judged and the ones whose rows could tell something (not all clean)."""

    def __init__(self, world: World, connectives: Connectives) -> None:
        self.world = world
        self.connectives = connectives
        self.judged: Counter[str] = Counter()
        self.telling: Counter[str] = Counter()
        self.skipped: Counter[str] = Counter()
        # Per rule type, the generated rules judged and those with no row on the fixture.
        self.rules: Counter[str] = Counter()
        self.empty: Counter[str] = Counter()
        # Leaves R0 reads as unknown on a resolved destination: what makes an unknown testable.
        self.unknown_leaves = 0

    def judge(self, family: Family, rules: dict[str, dict[str, Any]], relation: str) -> Judge | None:
        rules_text = "".join(rule_table(name, table) for name, table in rules.items())
        try:
            judge = Judge(self.world, rules_text, family.base)
        except ConfigError:
            self.skipped[relation] += 1
            return None
        self.judged[relation] += 1
        return judge

    def tell(self, relation: str, *rows: Rows) -> None:
        if any(rows):
            self.telling[relation] += 1

    # R0, R1 and R8: each selector of the rule (`select`, `from`, a predicate, `unless`) as the
    # selection of an edge rule.

    def composes(self, family: Family) -> list[str]:
        """R0. A selection's truth at each destination is its leaves' truths joined by the
        connectives table; each leaf's truth is read from a rule that selects that leaf alone."""
        if not family.selections:
            return []
        core = family.edge_core(keep_from=False)
        leaf_texts = list(dict.fromkeys(text(leaf) for whole in family.selections for leaf in leaves(parse(whole))))
        rules = {"universe": core}
        rules |= {f"whole{at}": {**core, "select": whole} for at, whole in enumerate(family.selections)}
        rules |= {f"leaf{at}": {**core, "select": leaf} for at, leaf in enumerate(leaf_texts)}
        judge = self.judge(family, rules, "R0")
        if judge is None:
            return []
        universe = judge.rows("universe")
        leaf_rows = {leaf: judge.rows(f"leaf{at}") for at, leaf in enumerate(leaf_texts)}
        truth_of = {VIOLATION: "true", UNKNOWN_SELECTION: "unknown", CLEAN: "false"}
        judged_of = {"true": VIOLATION, "unknown": UNKNOWN_SELECTION, "false": CLEAN}
        out = []
        for at, selection in enumerate(family.selections):
            node = parse(selection)
            whole = judge.rows(f"whole{at}")
            self.tell("R0", whole)
            for key, (judgment, _) in universe.items():
                # An unresolved destination tells false from not false only.
                if judgment != VIOLATION:
                    continue

                def leaf(found: Node, key: Key = key) -> str:
                    truth = truth_of[outcome(leaf_rows[text(found)], key)]
                    self.unknown_leaves += truth == "unknown"
                    return truth

                expected = judged_of[self.connectives.truth(node, leaf)]
                if outcome(whole, key) != expected:
                    out.append(f"R0 {key}: `{selection}` gives {outcome(whole, key)}, its leaves give {expected}")
        return out

    def negation_splits(self, family: Family) -> list[str]:
        """R1. With the edge rule's `from` kept and no predicate, each destination it sees is a
        violation of exactly one of `S` and `not (S)`, or an unknown selection of both where `S`
        is unknown; `not (not (S))` gives the rows of `S`."""
        if not family.selections:
            return []
        core = family.edge_core()
        rules = {"seen": core}
        for at, selection in enumerate(family.selections):
            rules[f"yes{at}"] = {**core, "select": selection}
            rules[f"no{at}"] = {**core, "select": f"not ({selection})"}
            rules[f"twice{at}"] = {**core, "select": f"not (not ({selection}))"}
        judge = self.judge(family, rules, "R1")
        if judge is None:
            return []
        seen = judge.rows("seen")
        allowed = {
            VIOLATION: {(VIOLATION, CLEAN), (CLEAN, VIOLATION), (UNKNOWN_SELECTION, UNKNOWN_SELECTION)},
            UNKNOWN_SELECTION: {
                (UNKNOWN_SELECTION, CLEAN),
                (CLEAN, UNKNOWN_SELECTION),
                (UNKNOWN_SELECTION, UNKNOWN_SELECTION),
            },
            UNRESOLVED: {(UNRESOLVED, CLEAN), (CLEAN, UNRESOLVED), (UNRESOLVED, UNRESOLVED)},
        }
        out = []
        for at, selection in enumerate(family.selections):
            yes, no, twice = (judge.rows(f"{name}{at}") for name in ("yes", "no", "twice"))
            self.tell("R1", yes, no)
            if yes != twice:
                diff = sorted(set(yes.items()) ^ set(twice.items()))[:3]
                out.append(f"R1: `not (not ({selection}))` differs from it at {diff}")
            if not (set(yes) | set(no)) <= set(seen):
                out.append(f"R1: `{selection}` judges a destination the rule with no selection does not see")
            for key, (judgment, _) in seen.items():
                pair = (outcome(yes, key), outcome(no, key))
                if pair not in allowed.get(judgment, {pair}):
                    out.append(f"R1 {key}: seen {judgment}, `S` and `not (S)` give {pair} for `{selection}`")
        return out

    def predicate_is_a_conjunct(self, family: Family) -> list[str]:
        """R2. `select = S, deny = P` against `select = S and P` (`allow` and `require` negated):
        the same violations; an unknown violation of the first is an unknown selection of the
        second where `S` holds; a clean destination of the first is clean in the second."""
        if not family.predicates:
            return []
        core = family.edge_core()
        verdict = " or ".join(
            f"({value})" if key == "deny" else f"not ({value})" for key, value in family.predicates.items()
        )
        rules = {
            "selected": {**core, **({"select": family.select} if family.select else {})},
            "predicate": {**core, **({"select": family.select} if family.select else {}), **family.predicates},
            "conjunct": {**core, "select": conjunction(family.select, verdict)},
        }
        judge = self.judge(family, rules, "R2")
        if judge is None:
            return []
        selected, predicate, joined = (judge.rows(name) for name in rules)
        self.tell("R2", predicate)
        out = []
        expected_of = {VIOLATION: VIOLATION, UNKNOWN_VIOLATION: UNKNOWN_SELECTION, CLEAN: CLEAN}
        for key in set(selected) | set(predicate) | set(joined):
            chosen, judged, conjunct = outcome(selected, key), outcome(predicate, key), outcome(joined, key)
            if chosen == VIOLATION:
                good = conjunct == expected_of.get(judged)
            elif chosen == UNKNOWN_SELECTION:
                good = judged == UNKNOWN_SELECTION and conjunct in (UNKNOWN_SELECTION, CLEAN)
            elif chosen == UNRESOLVED:
                good = judged == UNRESOLVED and conjunct in (UNRESOLVED, CLEAN)
            else:
                good = judged == CLEAN and conjunct == CLEAN
            if not good:
                out.append(f"R2 {key}: selection {chosen}, with the predicate {judged}, joined {conjunct}")
        return out

    def membership_expands(self, family: Family) -> list[str]:
        """R8. `x in [a, b]` gives the rows of `x == a or x == b`, and `not in` of its `not`."""
        pairs = [(whole, text(expand_membership(parse(whole)))) for whole in family.selections]
        pairs = [(whole, spelled) for whole, spelled in pairs if expand_membership(parse(whole)) != parse(whole)]
        if not pairs:
            return []
        core = family.edge_core()
        rules = {}
        for at, (whole, spelled) in enumerate(pairs):
            rules[f"listed{at}"] = {**core, "select": whole}
            rules[f"expanded{at}"] = {**core, "select": spelled}
        judge = self.judge(family, rules, "R8")
        if judge is None:
            return []
        out = []
        for at, (whole, spelled) in enumerate(pairs):
            listed, expanded = judge.rows(f"listed{at}"), judge.rows(f"expanded{at}")
            self.tell("R8", listed, expanded)
            if listed != expanded:
                diff = sorted(set(listed.items()) ^ set(expanded.items()))[:3]
                out.append(f"R8: `{whole}` and `{spelled}` differ at {diff}")
        return out

    # R3 to R5: siblings in the rule's own type.

    def staging(self, family: Family) -> list[str]:
        """R3. `from = F, select = S` gives the rows of `select = F and S`, of `S and F` with the
        parts of `S` reversed, and of `not ((not F) or (not S))`, one part that is never staged."""
        parts = [part for part in (family.start, family.select) if part]
        if not parts:
            return []
        base = family.without("from", "select", "uses_at_import")
        node = parse(conjunction(*parts))
        reversed_parts = list(reversed(node.parts)) if isinstance(node, And) else [node]
        one_part = "not (" + " or ".join(f"(not ({part}))" for part in parts) + ")"
        rules = {
            "staged": {**family.without("uses_at_import")},
            "joined": {**base, "select": conjunction(*parts)},
            "reversed": {**base, "select": conjunction(*(text(part) for part in reversed_parts))},
            "one-part": {**base, "select": one_part},
        }
        judge = self.judge(family, rules, "R3")
        if judge is None:
            return []
        staged = judge.rows("staged")
        self.tell("R3", staged)
        out = []
        for name in ("joined", "reversed", "one-part"):
            other = judge.rows(name)
            if other != staged:
                diff = sorted(set(staged.items()) ^ set(other.items()))[:3]
                out.append(f"R3: `{name}` differs from the staged rule at {diff}")
        return out

    def unless_subtracts(self, family: Family) -> list[str]:
        """R4. A rule with `unless = U` gives the rows of the rule without it, less each
        violation and unknown violation where `U` is true, read from an edge rule that selects
        `U` alone. An unknown `U` excuses nothing."""
        if not family.unless or family.mode in ("use-shape", "inventory-classify"):
            return []
        core = family.edge_core(keep_from=False)
        rules = {
            "with": family.without("uses_at_import"),
            "without": family.without("unless", "uses_at_import"),
            "excuse": {**core, "select": family.unless},
        }
        judge = self.judge(family, rules, "R4")
        if judge is None:
            return []
        excusing, plain, excuse = (judge.rows(name) for name in rules)
        self.tell("R4", plain)
        out = []
        for key in set(plain) | set(excusing):
            judgment = outcome(plain, key)
            excused = judgment in (VIOLATION, UNKNOWN_VIOLATION) and outcome(excuse, key) == VIOLATION
            expected = plain.get(key) if not excused else None
            if excusing.get(key) != expected:
                out.append(
                    f"R4 {key}: without `unless` {judgment}, `{family.unless}` gives {outcome(excuse, key)}, with it {outcome(excusing, key)}"
                )
        return out

    def identity_is_a_label(self, family: Family) -> list[str]:
        """R5. One identity column more changes no (edge, destination, judgment) and keeps the
        other columns; an inventory, which keeps the first row of each identity, keeps at least
        its rows."""
        identity = list(family.table.get("identity", []))
        column = next(ref for ref in ("edge.kind", "src.module", "dst.module", "edge.surface") if ref not in identity)
        rules = {"plain": family.table, "wider": {**family.table, "identity": [*identity, column]}}
        judge = self.judge(family, rules, "R5")
        if judge is None:
            return []
        plain, wider = judge.rows("plain"), judge.rows("wider")
        self.tell("R5", plain)
        narrowed = {
            key: (judgment, found[: len(identity)] if found else found) for key, (judgment, found) in wider.items()
        }
        if family.mode == "inventory-classify":
            return [] if set(plain.items()) <= set(narrowed.items()) else ["R5: the wider inventory loses a row"]
        if narrowed == plain:
            return []
        return [f"R5: one identity column more changes {sorted(set(plain.items()) ^ set(narrowed.items()))[:3]}"]

    def unknown_types_mark(self, family: Family) -> list[str]:
        """R9. A signature rule with `unknown_types = L` gives an unknown type exactly where its
        selection is not false, the destination is resolved and either its symbol is in `L` (an
        edge rule selecting `dst.symbol in L` tells which) or the rule with no list already gives
        one; anywhere else it gives the row of the rule whose list names nothing. (An empty list
        reads as the type's default list.)"""
        if family.mode != "signature":
            return []
        listed = family.table.get("unknown_types") or UNKNOWN_TYPES
        plain = family.without("unknown_types", "deny", "unless")
        rules = {
            "listed": {**family.table, "unknown_types": listed},
            "unlisted": {**family.table, "unknown_types": [NOTHING]},
            "selected": {**plain, "unknown_types": [NOTHING]},
            "named": {**family.edge_core(keep_from=False), "select": f"dst.symbol in {render_toml(listed)}"},
        }
        judge = self.judge(family, rules, "R9")
        if judge is None:
            return []
        with_list, without, selected, named = (judge.rows(name) for name in rules)
        self.tell("R9", with_list)
        out = []
        for key in set(with_list) | set(without) | set(selected):
            chosen = outcome(selected, key)
            if chosen in (CLEAN, UNRESOLVED):
                expected = outcome(without, key)
            elif outcome(named, key) == VIOLATION:
                expected = "unknown-type"
            else:
                expected = outcome(without, key)
            if outcome(with_list, key) != expected:
                out.append(f"R9 {key}: listed {outcome(with_list, key)}, expected {expected} (selection {chosen})")
        return out

    # R6: candidates.

    def candidates(self, family: Family) -> list[str]:
        """R6. An edge rule with no selection and no predicate has one row per destination of each
        edge of its kinds and contexts, counted from the graph's edges; every rule of the family
        judges only among them."""
        core = family.edge_core(keep_from=False)
        rules = {"seen": core, "rule": family.table}
        judge = self.judge(family, rules, "R6")
        if judge is None:
            return []
        seen, rule = judge.rows("seen"), judge.rows("rule")
        self.tell("R6", seen)
        self.rules[family.mode] += 1
        self.empty[family.mode] += not rule
        expected = self.counted(judge, "seen")
        out = []
        found = Counter(judgment == UNRESOLVED for judgment, _ in seen.values())
        if judge.config.rules["seen"].endpoint != "origin":
            # A hop or a surface that names nothing is unresolved: only the total is counted.
            found, expected = Counter({False: found.total()}), Counter({False: expected.total()})
        if found != expected:
            out.append(f"R6: {dict(found)} rows (unresolved: True), the graph's edges give {dict(expected)}")
        if not set(rule) <= set(seen):
            out.append(
                f"R6: the rule judges destinations its kinds and contexts do not hold: {sorted(set(rule) - set(seen))[:3]}"
            )
        return out

    def counted(self, judge: Judge, name: str) -> Counter[bool]:
        """Per unresolved or not, the destinations of rule `name`'s kinds and contexts."""
        rule = judge.rule(name)
        spec = judge.config.rules[name]
        include = frozenset(spec.contexts_include)
        exclude = frozenset(spec.contexts_exclude or ())
        kinds = frozenset(rule.kinds())
        out: Counter[bool] = Counter()
        for edge in self.world.graph.edges:
            if kinds and edge.kind not in kinds:
                continue
            if edge.resolution is Resolution.NOT_APPLICABLE:
                continue
            contexts = edge.contexts or frozenset({Context.PROD})
            if (include and not contexts & include) or contexts & exclude:
                continue
            if spec.endpoint == "via":
                out[False] += len(edge.via)
            elif spec.endpoint == "surface":
                out[False] += 1
            elif edge.resolution is Resolution.UNRESOLVED or not edge.targets:
                out[True] += 1
            else:
                out[False] += len(edge.targets)
        return +out

    def all(self, family: Family) -> list[str]:
        out = []
        for relation in (
            self.composes,
            self.negation_splits,
            self.predicate_is_a_conjunct,
            self.membership_expands,
            self.staging,
            self.unless_subtracts,
            self.identity_is_a_label,
            self.candidates,
            self.unknown_types_mark,
        ):
            out.extend(relation(family))
        return out


# The fixture's config with a field required on every product that only billing's modules set: an
# unknown on a resolved endpoint, which the fixture's own fields give on few destinations. Without
# it, nearly every unknown a relation meets is an unresolved destination, where a selection tells
# false apart from not false only.
UNSET = (
    CONFIG.replace(
        "[fields]\n", '[fields]\nphase = { type = "enum", values = ["alpha"], required_on = ["products/**"] }\n'
    )
    + '\n[[assign]]\nmatch = "products/billing/**"\nset = { phase = "alpha" }\n'
)
UNSET_REFS = [*REFS, *["src.phase", "dst.phase"] * 8]


def generated_families(world: World) -> Iterable[Family]:
    """Families from the generators of `test_evaluator.py`: tables of each judged rule type over
    the fixture's config (`Tables`), over it with a field nothing sets on some products (`UNSET`),
    then over configs that differ outside `[rules]` (`Varied`)."""
    tables = Tables(random.Random(20261113))
    for mode in JUDGED * ROUNDS:
        yield loaded(world, tables, mode, CONFIG)
    tables = Tables(random.Random(20261115), UNSET_REFS)
    for mode in JUDGED * ROUNDS:
        yield loaded(world, tables, mode, UNSET)
    rng = random.Random(1113)
    made = 0
    while made < VARIED:
        varied = Varied(rng)
        base = varied.config()
        try:
            world.config("", base)
        except ConfigError:
            continue
        made += 1
        yield loaded(world, Tables(rng, varied.refs), rng.choice(JUDGED), base)


def loaded(world: World, tables: Tables, mode: str, base: str) -> Family:
    """A table of `mode` that loads over `base`."""
    while True:
        table = tables.table(mode)
        # A rule that names a shape policy reads its predicates from it, and a sibling sets them.
        if "shape" in table:
            continue
        try:
            judge = Judge(world, rule_table("generated", table), base)
        except ConfigError:
            continue
        return Family(mode, table, base, Seen(judge, "generated"))


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return World(tmp_path_factory.mktemp("relations"))


def test_generated_rules_keep_the_relations_between_their_siblings(world: World) -> None:
    relations = Relations(world, Connectives(CONNECTIVES))
    failures = []
    for family in generated_families(world):
        failures.extend(f"{found}\n{rule_table('generated', family.table)}" for found in relations.all(family))
    assert failures == []
    # A relation over destinations every sibling judges clean proves nothing: each relation must
    # have rows to hold in enough families, and every sibling config must load.
    assert relations.skipped == Counter()
    assert relations.unknown_leaves >= UNKNOWN_LEAVES
    assert [name for name, floor in TELLING.items() if relations.telling[name] < floor] == [], relations.telling


ExportRow = tuple[Any, ...]


def apart(left: Counter[Any], right: Counter[Any]) -> Counter[Any]:
    """The rows one side holds more often than the other."""
    return (left - right) + (right - left)


class ExportOrder:
    """R7. An export rule judged with its `exports` in the other order, and with each
    `on_unknown`: the order changes no row; `on_unknown` changes the form of a row that is not
    definite (an unknown finding, ledgerable or not, or an incomplete), never its key (symbol,
    module, trail), and no definite finding."""

    ON_UNKNOWN = ("finding", "block", "incomplete")

    def __init__(self, world: World) -> None:
        self.world = world
        self.judged = 0
        self.telling = 0

    def rows(self, judge: Judge, name: str) -> list[ExportRow]:
        found = judge.ctx.evaluator.export(judge.ctx.plan.index[name])
        return [tuple(tuple(item) if isinstance(item, list) else item for item in row) for row in found]

    @staticmethod
    def definite(row: ExportRow) -> bool:
        return row[0] == "finding" and not row[7]

    @staticmethod
    def key(row: ExportRow) -> tuple[Any, ...]:
        return row[1], row[2], row[4]

    def check(self, table: dict[str, Any], extra: str) -> list[str]:
        exports = table.get("exports", ["facade", "adapter"])
        rules = {"as-drawn": {**table, "exports": exports}, "reversed": {**table, "exports": exports[::-1]}}
        rules |= {mode: {**table, "exports": exports, "on_unknown": mode} for mode in self.ON_UNKNOWN}
        rules_text = extra + "".join(rule_table(name, rule) for name, rule in rules.items())
        judge = Judge(self.world, rules_text, CONFIG)
        self.judged += 1
        found = {name: self.rows(judge, name) for name in rules}
        self.telling += bool(found["as-drawn"])
        out = []
        if Counter(found["as-drawn"]) != Counter(found["reversed"]):
            out.append(
                f"R7: `exports` in the other order changes {apart(Counter(found['as-drawn']), Counter(found['reversed']))}"
            )
        keys = {mode: Counter(self.key(row) for row in found[mode]) for mode in self.ON_UNKNOWN}
        definite = {mode: Counter(row for row in found[mode] if self.definite(row)) for mode in self.ON_UNKNOWN}
        for mode in self.ON_UNKNOWN[1:]:
            if keys[mode] != keys["finding"]:
                out.append(f"R7: on_unknown {mode} changes the keys {apart(keys[mode], keys['finding'])}")
            if definite[mode] != definite["finding"]:
                out.append(
                    f"R7: on_unknown {mode} changes a definite finding {apart(definite[mode], definite['finding'])}"
                )
        unledgered = Counter(row[:6] + row[7:] for row in found["finding"])
        if unledgered != Counter(row[:6] + row[7:] for row in found["block"]):
            out.append("R7: on_unknown block changes more than whether an unknown finding is ledgerable")
        return out


def export_tables(world: World) -> Iterable[tuple[dict[str, Any], str]]:
    """Export tables as `Tables.rules` draws them: most with a signature rule they leave slots to."""
    tables = Tables(random.Random(20261114))
    made = 0
    while made < EXPORTS:
        extra = ""
        named = ""
        if tables.rng.random() < 0.7:
            named = "owner"
            extra = rule_table(named, {**tables.table("signature"), "ledger": False})
        table = {key: value for key, value in tables.table("export", named).items() if key not in UNJUDGED_KEYS}
        table["ledger"] = False
        try:
            world.config(extra + rule_table("generated", table))
        except ConfigError:
            continue
        made += 1
        yield table, extra


def test_generated_export_rules_keep_their_rows_across_order_and_unknown_form(world: World) -> None:
    order = ExportOrder(world)
    failures = []
    for table, extra in export_tables(world):
        failures.extend(f"{found}\n{extra}{rule_table('generated', table)}" for found in order.check(table, extra))
    assert failures == []
    assert order.telling >= EXPORTS // 2
