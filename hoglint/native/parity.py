"""Semantic parity of the native engine against the Python engine, field by field.

Both engines read the same facts. Every core edge is compared in graph order and as a multiset,
with every field and attr; symbol facts and module tables too. Exit 0 when nothing differs.

Usage: python -m hoglint.native.parity --repo <root> [--work <dir>]
"""

from __future__ import annotations

import sys
import json
import argparse
import tempfile
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoglint.config import CONFIG_FILE, load_config
from hoglint.core.analysis import AnalysisOptions, AnalysisResult, CoreAnalysis
from hoglint.facts import Extractor, site_packages_files
from hoglint.native.config import link_config
from hoglint.native.normal import FIELDS, Row, edge_rows
from hoglint.source import GraphSource, active_packs

EXAMPLES = 5


@dataclass(frozen=True)
class RowDiff:
    """Two row lists compared in order and as multisets; `fields` counts positional differences."""

    name: str
    left: int
    right: int
    same_position: int
    missing: int
    extra: int
    fields: tuple[tuple[str, int], ...]
    examples: tuple[tuple[Row, Row], ...]

    @property
    def equal(self) -> bool:
        return self.left == self.right == self.same_position and not self.missing and not self.extra

    def render(self) -> str:
        verdict = "equal" if self.equal else "DIFFERENT"
        out = [
            f"{self.name}: {verdict}  python {self.left}  native {self.right}  same position {self.same_position}"
            f"  missing {self.missing}  extra {self.extra}"
        ]
        out.extend(f"  field {name}: {count}" for name, count in self.fields)
        for left, right in self.examples:
            out.append(f"  python {json.dumps(left, ensure_ascii=False)}")
            out.append(f"  native {json.dumps(right, ensure_ascii=False)}")
        return "\n".join(out)


def diff_rows(name: str, left: Sequence[Row], right: Sequence[Row], fields: Sequence[str] = ()) -> RowDiff:
    left_text = [json.dumps(row, ensure_ascii=False) for row in left]
    right_text = [json.dumps(row, ensure_ascii=False) for row in right]
    same = sum(1 for a, b in zip(left_text, right_text) if a == b)
    missing = Counter(left_text) - Counter(right_text)
    extra = Counter(right_text) - Counter(left_text)
    counts: Counter[str] = Counter()
    examples: list[tuple[Row, Row]] = []
    for a, b in zip(left, right):
        if a == b:
            continue
        if len(examples) < EXAMPLES:
            examples.append((a, b))
        for field, x, y in zip(fields, a, b):
            if x != y:
                counts[field] += 1
    return RowDiff(
        name,
        len(left),
        len(right),
        same,
        sum(missing.values()),
        sum(extra.values()),
        tuple(counts.most_common()),
        tuple(examples),
    )


class LinkBinary:
    """The `hoglint-link` debug binary, built next to `hoglint-facts`."""

    def __init__(self, work: Path) -> None:
        self.binary = Extractor.find_binary().with_name("hoglint-link")
        self.work = work

    def run(self, facts: Path, config: dict[str, Any]) -> tuple[list[Row], list[Any]]:
        config_path = self.work / "link.json"
        config_path.write_text(json.dumps(config))
        edges, symbols = self.work / "native-edges.jsonl", self.work / "native-symbols.jsonl"
        command = [str(self.binary), str(facts), str(config_path), "--edges", str(edges), "--symbols", str(symbols)]
        subprocess.run(command, check=True)
        with open(edges) as handle:
            edge_list = [json.loads(line) for line in handle]
        with open(symbols) as handle:
            symbol_list = [json.loads(line) for line in handle]
        return edge_list, symbol_list


class Parity:
    def __init__(self, repo: Path, work: Path) -> None:
        self.repo = repo
        self.work = work

    def python_core(self) -> AnalysisResult:
        config = load_config(self.repo, self.repo / CONFIG_FILE)
        source = GraphSource(self.work)
        options = AnalysisOptions(
            repo=self.repo,
            config=source.core_config(config),
            work_dir=self.work,
            needs=source.fact_needs(active_packs(config)),
        )
        return CoreAnalysis(options).run()

    def run(self) -> list[RowDiff]:
        analysis = self.python_core()
        core_config = analysis.build.config
        external = site_packages_files(core_config.external_packages)
        config = link_config(core_config, analysis.inventory.analyzed, external)
        native_edges, native_symbols = LinkBinary(self.work).run(self.work / "facts.jsonl", config)
        graph = analysis.graph
        python_symbols = [[name, facts["kind"], facts["path"]] for name, facts in graph.symbols.items()]
        python_modules = [[module, path] for module, path in graph.modules.items()]
        return [
            diff_rows("core edges", edge_rows(graph.edges), native_edges, FIELDS),
            diff_rows("symbols", python_symbols, native_symbols[2:]),
            diff_rows("modules", python_modules, native_symbols[1]["modules"]),
            diff_rows("first-party", [sorted(analysis.build.first_party.roots)], [native_symbols[0]["first_party"]]),
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    work = args.work or Path(tempfile.mkdtemp(prefix="hoglint-native-parity-"))
    work.mkdir(parents=True, exist_ok=True)
    diffs = Parity(args.repo.resolve(), work).run()
    sys.stdout.write("\n".join(diff.render() for diff in diffs) + "\n")
    sys.exit(0 if all(diff.equal for diff in diffs) else 1)


if __name__ == "__main__":
    main()
