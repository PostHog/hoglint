from __future__ import annotations

from pathlib import Path

import pytest

from hoglint.config import ConfigError
from hoglint.config.explain import ConfigExplainer

from rules.support import config_from

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
        # a set holds strings in both engines: 1 and true have no one text form
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
        # limits of the config, which both engines judge alike
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
        # a key the rule's mode never reads: each engine would give it a meaning of its own
        (
            "[rules.other]\nprimitive = 'inventory'\non = 'module'\nexactly_one = 'package'\n"
            "from = \"src.module == 'a'\"\nledger = false\n",
            "an inventory over entities (`exactly_one`) does not read `from`",
        ),
        (
            "[rules.other]\nprimitive = 'inventory'\nexactly_one = 'package'\nkinds = ['import']\nledger = false\n",
            "an inventory over entities (`exactly_one`) does not read `kinds`",
        ),
        (
            "[rules.other]\nprimitive = 'inventory'\nclassify = 'c.toml'\nexactly_one = 'package'\nledger = false\n",
            "an inventory over edges (`classify`) does not read `exactly_one`",
        ),
        (
            "[rules.other]\nprimitive = 'inventory'\nclassify = 'c.toml'\non = 'module'\nledger = false\n",
            "an inventory over edges (`classify`) does not read `on`",
        ),
        ("[rules.other]\nprimitive = 'layout'\nremedy = 'move it'\nledger = false\n", "a layout rule does not read"),
        ("[rules.other]\nprimitive = 'cycle'\nremedy = 'cut it'\nledger = false\n", "a cycle rule does not read"),
        (
            "[rules.other]\nprimitive = 'use-shape'\nview = 'refs'\nledger = false\n",
            "a use-shape rule does not read `view`",
        ),
        # an inventory over entities reads entity selectors, whether or not it names `on`
        (
            "[rules.other]\nprimitive = 'inventory'\nexactly_one = 'package'\nselect = \"src.tier == 'core'\"\n"
            "ledger = false\n",
            "unknown field 'src.tier'",
        ),
        ("[discovery]\nsource_root = ['.']\n", "unknown key 'source_root'"),
        ("[unknown_section]\nx = 1\n", "unknown section"),
    ],
)
def test_config_errors_before_any_run(tmp_path: Path, root: str, problem: str) -> None:
    with pytest.raises(ConfigError) as error:
        config_from(tmp_path, BASE + root, {"preset.toml": PRESET})
    assert problem in str(error.value)


def test_pack_is_found_and_unknown_pack_fails(tmp_path: Path) -> None:
    config = config_from(tmp_path, 'schema = 1\nextends = ["hoglint:django"]\n[django]\nsettings = "x"\n')
    assert "model-label" in config.edge_kinds
    with pytest.raises(ConfigError, match="hoglint:nope"):
        config_from(tmp_path / "other", 'schema = 1\nextends = ["hoglint:nope"]\n')
