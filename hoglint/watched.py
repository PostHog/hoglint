"""`hoglint watched`: per group of a level, the claim, the verdict at this tree and the watched files,
as one JSON object (format 1) that test selection reads.

A group's watched files are a pure function of the tree, the policy and the engine:
- every path a coverage rule the level requires needs, with what it cannot tell kept as unknowns;
  a location that one watched file covers (a `: location` cover) stands for its entry: the
  package's `__init__.py`, else the module files directly inside the directory that the cover
  keeps, else every module under it;
- every tracked file under the level's `watch_zones` inside the group's zone root, whatever reads
  it (non-Python files too);
- the `__init__.py` of every package above a watched file, which Python runs on import.
A path belongs to the group of its module, so a group's paths outside its directory appear too.

The answer is complete only when nothing unknown can make a set too small: a claim that cannot be
read, an unknown that no group owns, a file that cannot be read. A selector widens on anything
less than a complete answer.
"""

from __future__ import annotations

import re
import json
import bisect
import posixpath
from dataclasses import dataclass, field
from typing import Any

from hoglint.config.model import Config, CoverSpec, LevelSpec
from hoglint.globs import Braces, compile_glob
from hoglint.labels import Labeler
from hoglint.rules.base import RuleRun
from hoglint.rules.engine import UNREADABLE, RunResult
from hoglint.rules.entities import CoveragePrimitive
from hoglint.rules.levels import NOT_JUDGED, LevelEvaluator, LevelResult
from hoglint.selectors import Truth

FORMAT = 1
# Reasons beyond this many are cut to a count.
REASONS_SHOWN = 20
STRUCTURAL = "structural"


class WatchedError(ValueError):
    """The config names no level `watched` can answer for."""


def claimed_level(config: Config) -> LevelSpec | None:
    """The one level a group can claim, the one test selection reads; None when no level or
    several have `claimed_by`."""
    claimed = [level for level in config.levels.values() if level.claimed_by]
    return claimed[0] if len(claimed) == 1 else None


def pick_level(config: Config, name: str | None) -> LevelSpec:
    """The level asked for, else the one level a group can claim."""
    if name is not None:
        if name not in config.levels:
            raise WatchedError(f"no level {name!r}; the config has {', '.join(sorted(config.levels)) or 'none'}")
        return config.levels[name]
    level = claimed_level(config)
    if level is None:
        count = sum(1 for level in config.levels.values() if level.claimed_by)
        raise WatchedError(f"{count} levels have `claimed_by`, so --level must name one")
    return level


@dataclass(frozen=False, slots=True)
class GroupFiles:
    """One group's watched files, each with the first reason that asks for it."""

    files: dict[str, str] = field(default_factory=dict)

    def add(self, path: str, why: str) -> None:
        self.files.setdefault(path, why)


class WatchedSets:
    """The watched files of each group of one level, from one run."""

    def __init__(self, config: Config, result: RunResult, level: LevelSpec, tracked: list[str]) -> None:
        self.config = config
        self.result = result
        self.level = level
        self.tracked = sorted(tracked)
        self.labeler: Labeler = result.labeler
        self.modules = self.labeler.graph.modules
        self.path_to_module = {path: module for module, path in self.modules.items()}
        self.module_paths = sorted(self.path_to_module)
        self.evaluator = LevelEvaluator(level, self.labeler, result.runs)
        self.groups: dict[str, GroupFiles] = {}
        self.unplaced: list[str] = []

    def group_of_path(self, path: str, module: str | None) -> str | None:
        """The group of a path's own module, else of the module that needs it."""
        own = self.path_to_module.get(path)
        for candidate in (own, module):
            if candidate is not None:
                group = self.evaluator.group_of(candidate)
                if group is not None:
                    return group
        return None

    def add(self, path: str, module: str | None, why: str) -> None:
        group = self.group_of_path(path, module)
        if group is None:
            self.unplaced.append(path)
            return
        self.groups.setdefault(group, GroupFiles()).add(path, why)

    def coverage_runs(self) -> list[tuple[str, RuleRun]]:
        out = []
        for rule_id in self.level.requires:
            spec = self.config.rules.get(rule_id)
            run = self.result.runs.get(rule_id)
            if spec is not None and spec.primitive == "coverage" and run is not None:
                out.append((rule_id, run))
        return out

    def entry(self, location: str, cover: CoverSpec) -> list[str]:
        """The files that stand for a location core runs by name: its `__init__.py`, else the
        module files directly inside it that the cover keeps (or cannot tell it keeps), else every
        module under it."""
        init = f"{location}__init__.py"
        if init in self.path_to_module:
            return [init]
        inside = self.inside(location)
        direct = [path for path in inside if "/" not in path[len(location) :]]
        kept = [path for path in direct if self.keeps(cover, self.path_to_module[path])]
        return kept or inside or [init]

    def inside(self, directory: str) -> list[str]:
        """The module files under a directory (with its trailing slash), sorted."""
        start = bisect.bisect_left(self.module_paths, directory)
        end = bisect.bisect_left(self.module_paths, directory + "\U0010ffff")
        return self.module_paths[start:end]

    def keeps(self, cover: CoverSpec, module: str) -> bool:
        if cover.selector is None:
            return True
        resolve = self.labeler.entity_resolver(module, None)
        return cover.selector.evaluate(resolve) is not Truth.FALSE

    def add_required(self) -> None:
        for rule_id, run in self.coverage_runs():
            for path, (cover, module) in sorted(run.required.items()):
                why = f"{rule_id}: {CoveragePrimitive.why(cover)}"
                if not path.endswith("/"):
                    self.add(path, module, why)
                    continue
                if CoveragePrimitive.any_inside(path, cover):
                    files = self.entry(path, cover)
                else:
                    files = self.inside(path) or [f"{path}__init__.py"]
                for file in files:
                    self.add(file, module, why)

    def zone_roots(self) -> dict[str, str]:
        """root directory -> group, for each directory the zones' `within` patterns match that
        holds a module: the group of its first module."""
        patterns = [compile_glob(pattern, Braces.CAPTURE) for pattern in self.config.zones.within]
        roots: dict[str, str] = {}
        for path in sorted(self.path_to_module):
            root = self.root_of(path, patterns, roots)
            if root is None or root in roots:
                continue
            group = self.evaluator.group_of(self.path_to_module[path])
            if group is not None:
                roots[root] = group
        return roots

    @staticmethod
    def root_of(path: str, patterns: list[re.Pattern[str]], known: dict[str, str]) -> str | None:
        """The nearest directory above `path` that is a known root or that a pattern matches."""
        directory = posixpath.dirname(path)
        while directory:
            if directory in known or any(pattern.match(directory) for pattern in patterns):
                return directory
            directory = posixpath.dirname(directory)
        return None

    def add_zones(self) -> None:
        zones = self.config.zones.by_name()
        globs = [
            (name, compile_glob(path, Braces.ALTERNATION))
            for name in self.level.watch_zones
            for path in zones[name].paths
        ]
        if not globs:
            return
        roots = self.zone_roots()
        for path in self.tracked:
            directory = posixpath.dirname(path)
            while directory and directory not in roots:
                directory = posixpath.dirname(directory)
            if not directory:
                continue
            inside = path[len(directory) + 1 :]
            for name, pattern in globs:
                if pattern.match(inside):
                    self.groups.setdefault(roots[directory], GroupFiles()).add(path, f"{STRUCTURAL}: zone {name}")
                    break

    def add_parent_packages(self) -> None:
        """The `__init__.py` of each package above a watched file, in the same group."""
        for group, found in sorted(self.groups.items()):
            for path in sorted(found.files):
                directory = posixpath.dirname(path)
                while directory:
                    init = f"{directory}/__init__.py"
                    module = self.path_to_module.get(init)
                    if init != path and module is not None and self.evaluator.group_of(module) == group:
                        found.add(init, f"{STRUCTURAL}: the package above a watched file")
                    directory = posixpath.dirname(directory)

    def add_wrappers(self) -> None:
        """Limit 12: the file of each first-party wrapper trusted on a decorated item, in every
        group that watches the item's file."""
        for item, wrapper in self.result.wrappers:
            for found in self.groups.values():
                if item in found.files:
                    found.add(wrapper, f"limit-12: a wrapper of the registered item in {item}")

    def build(self) -> dict[str, GroupFiles]:
        self.add_required()
        self.add_wrappers()
        self.add_zones()
        self.add_parent_packages()
        return self.groups


def stopped_watching(before: dict[str, GroupFiles], after: dict[str, GroupFiles], tracked: set[str]) -> list[str]:
    """`group: path (was: why)` for each file still in the tree that a group watched before and
    no longer watches. A deleted file is no row: selection counts a deletion as a change anyway."""
    rows = []
    for group, found in sorted(before.items()):
        now = after.get(group, GroupFiles()).files
        for path, why in sorted(found.files.items()):
            if path not in now and path in tracked:
                rows.append(f"{group}: {path} (was: {why})")
    return rows


def cut(reasons: list[str]) -> list[str]:
    if len(reasons) <= REASONS_SHOWN:
        return reasons
    return [*reasons[:REASONS_SHOWN], f"and {len(reasons) - REASONS_SHOWN} more"]


@dataclass(frozen=True, slots=True)
class WatchedAnswer:
    """The JSON of `hoglint watched`, format 1."""

    data: dict[str, Any]

    def text(self) -> str:
        return json.dumps(self.data, indent=2, sort_keys=True) + "\n"


def problems_of(level: LevelSpec, result: RunResult, outcome: LevelResult, sets: WatchedSets) -> list[str]:
    """What makes the answer incomplete: a claim that cannot be read, an unknown of a rule the
    level reads that no group owns, a file that cannot be read, a watched path in no group."""
    out = []
    if outcome.claim_error:
        out.append(f"claims: {outcome.claim_error}")
    for rule_id in (*level.requires, UNREADABLE):
        run = result.runs.get(rule_id)
        if run is None:
            if rule_id != UNREADABLE:
                out.append(f"{rule_id} did not run")
            continue
        out.extend(f"{rule_id}: {item.reason}" for item in run.incomplete if not item.subjects)
    out.extend(f"no group owns the watched path {path}" for path in sorted(set(sets.unplaced)))
    return out


def watched_answer(
    config: Config, result: RunResult, level: LevelSpec, tracked: list[str], header: dict[str, str]
) -> WatchedAnswer:
    """The answer of one run. `header` holds the version, the engine id and the policy digest."""
    outcome = result.levels.get(level.name) or LevelResult(level.name)
    sets = WatchedSets(config, result, level, tracked)
    files = sets.build()
    problems = problems_of(level, result, outcome, sets)
    groups: dict[str, Any] = {}
    names = set(outcome.passing) | set(outcome.blocks) | set(outcome.claims)
    for group in sorted(names):
        passes = group in outcome.passing
        reasons = outcome.failing[group] if group in outcome.blocks else []
        if group in outcome.claims and group not in outcome.passing and group not in outcome.blocks:
            reasons = [NOT_JUDGED]
        found = files.get(group, GroupFiles())
        groups[group] = {
            "claimed": outcome.claims.get(group),
            "passes": passes,
            "reasons": cut(reasons),
            "watched": dict(sorted(found.files.items())),
        }
    data = {
        "format": FORMAT,
        **header,
        "level": level.name,
        "complete": not problems,
        "problems": problems,
        "groups": groups,
        "limits": [site.to_json() for site in result.limits],
    }
    return WatchedAnswer(data)
