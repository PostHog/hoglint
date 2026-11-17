from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hoglint.config import ConfigError
from hoglint.config.explain import ConfigExplainer
from hoglint.rules.plan import tree

from rules.support import (
    PRESET as PRESET_FILE,
    config_from,
)

OTHER_RULE = "[rules.other]\nprimitive = 'edge'\nledger = false\n"

BASE = """
schema = 1
extends = ["preset.toml"]
"""

PRESET = """
schema = 1

[fields]
package = { type = "id" }
tier = { type = "enum", values = ["core", "product"] }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}", tier = "product" }

[rules.cross]
primitive = "edge"
kinds = ["import"]
select = "src.package != dst.package"
deny = "dst.tier == 'product'"
identity = "module-pair"
growth_key = ["dst.module"]
"""


def test_patch_adds_to_a_preset_list_and_records_the_source(tmp_path: Path) -> None:
    config = config_from(
        tmp_path,
        BASE + '[rules.cross]\nkinds = { add = ["reference"] }\n',
        {"preset.toml": PRESET},
    )
    assert config.rules["cross"].kinds == ("import", "reference")
    assert config.provenance["rules.cross.kinds"] == "preset.toml, patched by hoglint.toml"
    explained = ConfigExplainer(config).render()
    assert 'kinds = ["import", "reference"]  # preset.toml, patched by hoglint.toml' in explained
    assert "# cross: identity module-pair (src.module, dst.module), growth by (dst.module)" in explained


def test_replace_swaps_the_whole_rule(tmp_path: Path) -> None:
    root = (
        BASE
        + """
[rules.cross]
replace = true
primitive = "edge"
kinds = ["reference"]
deny = "dst.tier == 'core'"
ledger = false
"""
    )
    config = config_from(tmp_path, root, {"preset.toml": PRESET})
    assert config.rules["cross"].kinds == ("reference",)
    assert config.rules["cross"].select is None


@pytest.mark.parametrize(
    "root, problem",
    [
        # a root redefinition cannot silently drop what the preset said
        ("[rules.cross]\nprimitive = 'edge'\nkinds = ['import']\nledger = false\n", "already defined in preset.toml"),
        ("[fields]\ntier = { type = 'enum', values = ['core'] }\n", "`fields.tier` is already defined"),
        (
            "[rules.other]\nprimitive = 'edge'\ndeny = \"dst.teir == 'core'\"\nledger = false\n",
            "did you mean 'dst.tier'",
        ),
        (
            "[rules.other]\nprimitive = 'edge'\ndeny = \"dst.tier == 'prodcut'\"\nledger = false\n",
            "did you mean 'product'",
        ),
        ("[rules.other]\nprimitive = 'edge'\nkinds = ['imprt']\nledger = false\n", "unknown edge kind 'imprt'"),
        ("[rules.other]\nprimitive = 'egde'\n", "unknown primitive 'egde'"),
        ("[rules.other]\nprimitive = 'edge'\ndenny = 'true'\nledger = false\n", "unknown key 'denny'"),
        ("[rules.other]\nprimitive = 'edge'\nidentity = 'module-pair'\n", "needs `growth_key`"),
        (
            "[rules.other]\nprimitive = 'edge'\nidentity = 'module-pair'\ngrowth_key = ['dst.symbol']\n",
            "not an identity column",
        ),
        ("[rules.other]\nprimitive = 'edge'\non_unresolved = 'ignore'\nledger = false\n", "no `ignore`"),
        # reach's `from` reads the start module alone: there is no edge to read
        (
            "[rules.other]\nprimitive = 'reach'\nview = 'file-imports'\nfrom = \"edge.kind == 'import'\"\nledger = false\n",
            "unknown field 'edge.kind'",
        ),
        # a module field's domain belongs to the module, whichever symbol asks
        (
            "[fields]\nlegacy = { type = 'bool', required_on = \"decl == 'class'\" }\n",
            "a module field's domain reads the module alone, not a symbol's decl",
        ),
        ("[[assign]]\nmatch = 'x/**'\nset = { tier = 'leaf' }\n", "'leaf' is not a value of field 'tier'"),
        ("[[assign]]\nmatch = 'x/**'\nset = { package = '{pkg}' }\n", "`{pkg}` is not captured"),
        # a set holds strings: 1 and true have no one text form
        (
            "[fields]\nowners = { type = 'set' }\n[[assign]]\nmatch = 'x/**'\nset = { owners = ['a', 1, true] }\n",
            "field 'owners' is a set of strings; 1 is not a string",
        ),
        (
            "[fields]\nowners = { type = 'set' }\n[[assign]]\nmatch = 'x/**'\nset = { owners = true }\n",
            "field 'owners' is a set of strings; True is not a string",
        ),
        (
            "[shapes.contract]\ndata = ['frozen-dataclass']\n[exports.facade]\npaths = ['facade/**']\n",
            "needs a shape policy",
        ),
        ("[levels.isolated]\ngroup_by = 'package'\nrequires = ['crosss']\n", "requires unknown rule 'crosss'"),
        (
            "[levels.isolated]\ngroup_by = 'package'\nrequires = ['cross']\nclaimed_by = { adapter = 'turbos' }\n",
            "unknown coverage adapter 'turbos'",
        ),
        # a module cover reads one module at a time: there is no edge to read
        (
            "[rules.other]\nprimitive = 'coverage'\ncovers = [\"edge.kind == 'import': module\"]\n"
            "inputs = { adapter = 'turbo', task = 'x' }\nledger = false\n",
            "unknown field 'edge.kind'",
        ),
        (
            "[rules.other]\nprimitive = 'coverage'\ncovers = [\"tier == 'core': modules\"]\n"
            "inputs = { adapter = 'turbo', task = 'x' }\nledger = false\n",
            "or `<entity selector>: module`",
        ),
        (
            "[levels.isolated]\ngroup_by = 'package'\nrequires = ['cross']\n"
            "claimed_by = { adapter = 'turbo', tsk = 'x' }\n",
            "unknown key 'tsk'",
        ),
        # an export rule leaves a slot only to a signature rule it names
        (
            "[rules.other]\nprimitive = 'export'\nslots_reported_by = ['crosss']\nledger = false\n",
            "names unknown rule 'crosss'",
        ),
        (
            "[rules.other]\nprimitive = 'export'\nslots_reported_by = ['cross']\nledger = false\n",
            "'cross' is no signature rule",
        ),
        # a renamed key or attribute reads nothing and names its new form
        (
            "[shapes.c]\nallow = ['class']\nallow_where = \"tier == 'core'\"\n",
            "`allow_where` is now `allow_symbols`",
        ),
        (
            "[rules.other]\nprimitive = 'export'\nleave_slots_to = ['cross']\nledger = false\n",
            "`leave_slots_to` is now `slots_reported_by`",
        ),
        (
            "[shapes.c]\nallow = ['class']\nwalk_where = \"tier != 'core'\"\n",
            "`walk_where` is now `owned_elsewhere`, with the reverse sign",
        ),
        (
            "[shapes.c]\nallow = ['class']\nbehavior = { kind = 'abc', where = \"tier == 'core'\" }\n",
            '`behavior` names a shape, as `behavior = "abc"`; its `where` is now `owned_elsewhere`',
        ),
        (
            "[rules.other]\nprimitive = 'export'\nunknown_types = ['typing.Any']\nledger = false\n",
            "`unknown_types` is now `unknown_types` of the shape policy each export names",
        ),
        # a behavior class is allowed only where its base is owned elsewhere, so the policy says where
        ("[shapes.c]\nallow = ['class']\nbehavior = 'abc'\n", "`behavior` needs `owned_elsewhere`"),
        (
            "[rules.other]\nprimitive = 'edge'\nledger = false\ndeny = \"src.handed_zones in ['x']\"\n",
            "unknown field 'src.handed_zones' (renamed 'src.handout_zones')",
        ),
        # a signature rule that names a policy reads what the policy says, and no copy of it
        (
            "[shapes.c]\nallow = ['class']\n[rules.other]\nprimitive = 'signature'\nshape = 'c'\n"
            "deny = \"dst.tier == 'core'\"\nledger = false\n",
            "`deny` comes from the policy `shape` names",
        ),
        (
            "[rules.other]\nprimitive = 'signature'\nshape = 'contract'\nledger = false\n",
            "shape policy 'contract' is not defined under [shapes]",
        ),
        (
            "[shapes.c]\ndeny = ['class']\n[rules.other]\nprimitive = 'signature'\nshape = 'c'\nledger = false\n",
            "[shapes.c] denies class, which `dst.kind` cannot read",
        ),
        (
            "[shapes.c]\nallow = ['class']\nmappings = ['builtins.dict']\n",
            "`mappings` and `opaque_payload` are read only by a signature rule that names this policy",
        ),
        # the named rule's selection splits into where it reads rows and which slots it judges
        (
            "[rules.mixed]\nprimitive = 'signature'\nselect = \"src.module == 'a' or edge.slot == 'return'\"\n"
            "ledger = false\n[rules.other]\nprimitive = 'export'\nslots_reported_by = ['mixed']\nledger = false\n",
            "mixes the source with the edge or the destination",
        ),
        # limits of the config
        (
            "[shapes.c]\nallow = ['class']\n"
            + "".join(f"[exports.e{at}]\npaths = ['x{at}/**']\nshape = 'c'\n" for at in range(65)),
            "65 exports; a config declares at most 64",
        ),
        # a path is cut at the length of the filled `within`, the matched text only without wildcards
        ("[zones]\nwithin = 'pkgs/*'\n", "`within = 'pkgs/*'` holds '*'"),
        (
            "[shapes.c]\nallow = ['class']\n[exports.e]\nwithin = '{top}/**'\npaths = ['x/**']\nshape = 'c'\n",
            "`within = '{top}/**'` holds '*'",
        ),
        ("[zones]\nwithin = ['products/{product}', 'pkgs/?']\n", "`within = 'pkgs/?'` holds '?'"),
        (
            "[rules.other]\nprimitive = 'edge'\nledger = false\ndeny = \"" + "not " * 33 + "src.module == 'a'\"\n",
            "nested more than 32 deep",
        ),
        (
            "[rules.other]\nprimitive = 'edge'\nledger = false\ndeny = \""
            + "(src.module == 'a' or src.module == 'b' and " * 20
            + "true"
            + ")" * 20
            + '"\n',
            "nested more than 32 deep",
        ),
        # a key the rule's mode never reads has no meaning (D52)
        (
            "[rules.other]\nprimitive = 'inventory'\non = 'module'\nexactly_one = 'package'\n"
            "from = \"src.module == 'a'\"\nledger = false\n",
            "`from` is not read by inventory rules with `exactly_one`",
        ),
        (
            "[rules.other]\nprimitive = 'inventory'\nexactly_one = 'package'\nkinds = ['import']\nledger = false\n",
            "`kinds` is not read by inventory rules with `exactly_one`",
        ),
        (
            "[rules.other]\nprimitive = 'inventory'\nclassify = 'c.toml'\nexactly_one = 'package'\nledger = false\n",
            "`exactly_one` is not read by inventory rules with `classify`",
        ),
        (
            "[rules.other]\nprimitive = 'inventory'\nclassify = 'c.toml'\non = 'module'\nledger = false\n",
            "`on` is not read by inventory rules with `classify`",
        ),
        (
            "[rules.other]\nprimitive = 'layout'\non_unresolved = 'finding'\nledger = false\n",
            "`on_unresolved` is not read by layout rules",
        ),
        (
            "[rules.other]\nprimitive = 'use-shape'\nview = 'refs'\nledger = false\n",
            "`view` is not read by use-shape rules",
        ),
        # an inventory over entities reads entity selectors, whether or not it names `on`
        (
            "[rules.other]\nprimitive = 'inventory'\nexactly_one = 'package'\nselect = \"src.tier == 'core'\"\n"
            "ledger = false\n",
            "unknown field 'src.tier'",
        ),
        # `classify` names a file: a bool would run an edge inventory over a file named "False"
        (
            "[rules.other]\nprimitive = 'inventory'\nclassify = false\nexactly_one = 'package'\nledger = false\n",
            "`classify` is the path of a classification file, not False",
        ),
        ("[discovery]\nsource_root = ['.']\n", "unknown key 'source_root'"),
        # a condition's name stands where an expression stands, so it may not read as anything else
        ("[conditions]\npackage = \"src.package == 'a'\"\n", "'package' is already a field"),
        ("[conditions]\nmodule = \"src.package == 'a'\"\n", "'module' is already a field, an attribute"),
        ("[conditions]\nnone = \"src.package == 'a'\"\n", "'none' is already a field, an attribute or a keyword"),
        ("[conditions]\nsrc = \"src.package == 'a'\"\n", "'src' is already a field"),
        ('[conditions]\n"a.b" = "src.package == \'a\'"\n', "a condition's name is one word"),
        ('[conditions]\nbroken = "src.package =="\n', "conditions.broken: unexpected end"),
        # conditions are flat: one never uses another
        (
            "[conditions]\nmine = \"src.package == 'a'\"\nboth = \"mine and dst.package == 'a'\"\n"
            + OTHER_RULE
            + "deny = 'both'\n",
            "conditions.both: uses the condition 'mine'; a condition may not use another condition",
        ),
        ("[conditions]\nlonely = \"src.package == 'a'\"\n", "conditions.lonely: no selector uses this condition"),
        # the error names the rule and the condition
        (
            '[conditions]\ncrossing = "src.packag != dst.package"\n' + OTHER_RULE + "deny = 'crossing'\n",
            "rules.other.deny: in condition 'crossing': unknown field 'src.packag'",
        ),
        (
            '[conditions]\ncrossing = "src.package != dst.package"\n' + OTHER_RULE + "deny = 'crossing == true'\n",
            "condition 'crossing' is an expression and cannot be compared",
        ),
        ("[unknown_section]\nx = 1\n", "unknown section"),
        # a runner that cannot name the version would run some other engine as the base's
        ("[engine]\nrunner = 'uv run --with hoglint hoglint'\n", "engine.runner: needs {version}"),
        (
            OTHER_RULE + 'ways = [{ safety = "maybe", text = "fix it" }]\n',
            "rules.other.ways[0].safety: 'maybe' is not one of",
        ),
        (OTHER_RULE + 'ways = "fix it"\n', "rules.other.ways: must be a list of { safety, text } tables"),
        (OTHER_RULE + 'example = { finding = "a" }\n', "rules.other.example: must be a table with exactly the keys"),
    ],
)
def test_config_errors_before_any_run(tmp_path: Path, root: str, problem: str) -> None:
    with pytest.raises(ConfigError) as error:
        config_from(tmp_path, BASE + root, {"preset.toml": PRESET})
    assert problem in str(error.value)


def test_every_rule_of_the_preset_says_why_and_what_to_do_instead() -> None:
    """Every rule prints a cause and at least one way out; a rule whose ways hold no safe or careful
    fix ends with stopping to tell the person, so a reader is never left without a next step."""
    rules = tomllib.loads(PRESET_FILE.read_text())["rules"]
    for name, rule in rules.items():
        safeties = [way["safety"] for way in rule.get("ways", [])]
        assert rule.get("why") and safeties, name
        assert {"safe", "care"} & set(safeties) or safeties[-1] == "person", name


CROSSING = "src.package != dst.package and dst.tier == none"


def named_condition(tmp_path: Path, crossing: str, deny: str) -> tuple[str, dict[str, object] | None]:
    root = BASE + f'[conditions]\ncrossing = "{crossing}"\n' + OTHER_RULE + f'deny = "{deny}"\n'
    config = config_from(tmp_path, root, {"preset.toml": PRESET})
    deny_selector = config.rules["other"].deny
    assert deny_selector is not None
    return config.policy_hash, tree(deny_selector.node)


def test_a_condition_reads_as_its_text_in_parentheses_and_its_text_keys_the_policy(tmp_path: Path) -> None:
    used, named = named_condition(tmp_path / "named", CROSSING, "not crossing or dst.tier == 'core'")
    inline = config_from(
        tmp_path / "inline",
        BASE + OTHER_RULE + f"deny = \"not ({CROSSING}) or dst.tier == 'core'\"\n",
        {"preset.toml": PRESET},
    )
    inline_deny = inline.rules["other"].deny
    assert inline_deny is not None
    # the evaluator receives this tree: the same tree means it never sees a condition's name
    assert named == tree(inline_deny.node)
    changed, changed_tree = named_condition(
        tmp_path / "changed", "src.package != dst.package", "not crossing or dst.tier == 'core'"
    )
    assert changed != used
    assert changed_tree != named


EITHER = "src.package == 'a' or src.package == 'b'"
# (condition, deny naming it, the same deny inline without and with parentheses). A condition's
# parts join the enclosing `and` (or `or`), so a rule's staging sees each part on its own.
FLAT_CASES = {
    "and in and": (
        CROSSING,
        "crossing and dst.tier == 'core'",
        f"{CROSSING} and dst.tier == 'core'",
        f"({CROSSING}) and dst.tier == 'core'",
    ),
    "or in or": (
        EITHER,
        "crossing or dst.tier == 'core'",
        f"{EITHER} or dst.tier == 'core'",
        f"({EITHER}) or dst.tier == 'core'",
    ),
}


@pytest.mark.parametrize("case", FLAT_CASES)
def test_a_condition_joins_the_enclosing_and_or_or(tmp_path: Path, case: str) -> None:
    crossing, deny, inline, parenthesized = FLAT_CASES[case]
    _, named = named_condition(tmp_path / "named", crossing, deny)
    trees = []
    for name, text in (("inline", inline), ("parenthesized", parenthesized)):
        config = config_from(tmp_path / name, BASE + OTHER_RULE + f'deny = "{text}"\n', {"preset.toml": PRESET})
        selector = config.rules["other"].deny
        assert selector is not None
        trees.append(tree(selector.node))
    assert named == trees[0] == trees[1]


def test_pack_is_found_and_unknown_pack_fails(tmp_path: Path) -> None:
    config = config_from(tmp_path, 'schema = 1\nextends = ["hoglint:django"]\n[django]\nsettings = "x"\n')
    assert "model-label" in config.edge_kinds
    with pytest.raises(ConfigError, match="hoglint:nope"):
        config_from(tmp_path / "other", 'schema = 1\nextends = ["hoglint:nope"]\n')
