"""Primitives over entities and outside inputs: layout, inventory, coverage."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path

from hoglint.config.model import CoverSpec, RuleSpec
from hoglint.labels import fill, glob_match
from hoglint.model import Edge, Finding
from hoglint.rules.base import EdgeEnv, EdgeRule, IncompleteItem, RuleContext, RuleRun, project, render_value
from hoglint.selectors import Ref, State, Truth, Value

Resolver = Callable[[Ref], Value]


class EntityRule:
    """Shared emit and selection for rules whose subject is a module, symbol or path."""

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        self.ctx = ctx
        self.spec = spec
        self.run = RuleRun(spec)

    def resolver(self, module: str | None, symbol: str | None, subject: str) -> Resolver:
        base = self.ctx.labeler.entity_resolver(module, symbol)

        def resolve(ref: Ref) -> Value:
            if ref.path == ("subject",):
                return Value.known(subject)
            return base(ref)

        return resolve

    def emit(self, subject: str, module: str | None, message: str, resolve: Resolver) -> None:
        self.run.findings.append(
            Finding(
                rule=self.spec.id,
                identity=project(self.spec.identity, resolve),
                message=message,
                site=None,
                owners=self.ctx.owners_of(module),
                subjects=(module or subject,),
                ledgerable=self.spec.ledger,
            )
        )

    def incomplete(self, subject: str, module: str | None, reason: str) -> None:
        self.run.incomplete.append(IncompleteItem(self.spec.id, subject, reason, (module or subject,)))

    def entities(self) -> Iterator[tuple[str | None, str | None, str]]:
        """(module, symbol, subject) for every entity the rule looks at."""
        labeler = self.ctx.labeler
        if self.spec.on == "symbol":
            for symbol in sorted(self.ctx.graph.symbols):
                yield labeler.module_of_symbol(symbol), symbol, symbol
            return
        for module in sorted(self.ctx.graph.modules):
            yield module, None, module


class LayoutPrimitive(EntityRule):
    """Required files and zones exist, moved files are gone, selected entities meet `require`."""

    def evaluate(self) -> RuleRun:
        if self.spec.select is not None or self.spec.require is not None:
            self.check_entities()
        if self.spec.within and (self.spec.files or self.spec.absent):
            self.check_files()
        if self.spec.registry:
            self.check_registry()
        return self.run

    def check_entities(self) -> None:
        for module, symbol, subject in self.entities():
            resolve = self.resolver(module, symbol, subject)
            selected = self.spec.select.evaluate(resolve) if self.spec.select is not None else Truth.TRUE
            if selected is Truth.FALSE:
                continue
            if selected is Truth.UNKNOWN:
                self.incomplete(subject, module, "cannot tell whether this entity is selected")
                continue
            verdict = self.spec.require.evaluate(resolve) if self.spec.require is not None else Truth.FALSE
            if verdict is Truth.TRUE:
                continue
            suffix = f" does not meet `{self.spec.require.source}`" if self.spec.require is not None else ""
            self.emit(subject, module, f"{subject}{suffix}", resolve)

    def roots(self) -> list[str]:
        roots: set[str] = set()
        for path in self.ctx.graph.modules.values():
            captures = glob_match(self.spec.within + "/**", path)
            if captures is not None:
                roots.add(fill(self.spec.within, captures))
        return sorted(roots)

    def check_files(self) -> None:
        repo = self.ctx.repo_root
        for root in self.roots():
            module = self.ctx.labeler.path_to_module.get(f"{root}/__init__.py")
            resolve = self.resolver(module, None, root)
            if self.spec.select is not None and self.spec.select.evaluate(resolve) is not Truth.TRUE:
                continue
            for required in self.spec.files:
                if repo is None:
                    self.incomplete(root, module, "no repo root to check files against")
                    break
                if not (repo / root / required).exists():
                    self.emit(
                        f"{root}/{required}",
                        module,
                        f"{root} lacks {required}",
                        self.resolver(module, None, f"{root}/{required}"),
                    )
            for forbidden in self.spec.absent:
                if repo is not None and (repo / root / forbidden).exists():
                    self.emit(
                        f"{root}/{forbidden}",
                        module,
                        f"{root}/{forbidden} must be gone",
                        self.resolver(module, None, f"{root}/{forbidden}"),
                    )

    def check_registry(self) -> None:
        registry = self.ctx.config.registries[self.spec.registry]
        facts = self.ctx.labeler.facts
        base = registry.shape.removeprefix("abc:")
        for module, path in sorted(self.ctx.graph.modules.items()):
            if glob_match(registry.module, path) is None:
                continue
            attribute = f"{module}.{registry.attribute}"
            resolve = self.resolver(module, None, attribute)
            if not facts.has(attribute):
                self.emit(
                    attribute,
                    module,
                    f"{path} matches registry {registry.name} but defines no {registry.attribute}",
                    resolve,
                )
                continue
            for member in facts.get(attribute, "members").split(","):
                member = member.strip()
                if not member or not base:
                    continue
                ancestors, complete = facts.ancestors(member)
                if base in ancestors:
                    continue
                if not complete:
                    self.incomplete(member, module, f"cannot tell whether {member} subclasses {base}")
                    continue
                self.emit(
                    member,
                    module,
                    f"{member} in {attribute} does not subclass {base}",
                    self.resolver(module, member, member),
                )


class InventoryPrimitive(EntityRule):
    """Every selected edge carries a classification, and every classification still matches one."""

    def evaluate(self) -> RuleRun:
        if self.spec.exactly_one:
            self.check_exactly_one()
            return self.run
        self.check_classified()
        return self.run

    def check_exactly_one(self) -> None:
        name = self.spec.exactly_one
        for module, symbol, subject in self.entities():
            resolve = self.resolver(module, symbol, subject)
            if self.spec.select is not None:
                selected = self.spec.select.evaluate(resolve)
                if selected is Truth.UNKNOWN:
                    self.incomplete(subject, module, "cannot tell whether this entity is selected")
                if selected is not Truth.TRUE:
                    continue
            value = resolve(Ref((name,)))
            if value.state is State.UNKNOWN:
                self.incomplete(subject, module, f"{name} is unknown")
                continue
            count = len(value.data) if value.state is State.KNOWN and isinstance(value.data, frozenset) else 0
            if count != 1:
                self.emit(subject, module, f"{subject} has {count} {name} values, needs exactly one", resolve)

    def classifications(self) -> list[tuple[str, ...]]:
        repo = self.ctx.repo_root
        if repo is None or not (repo / self.spec.classify).is_file():
            return []
        path = repo / self.spec.classify
        data = json.loads(path.read_text()) if path.suffix == ".json" else tomllib.loads(path.read_text())
        return [tuple(str(v) for v in entry.get("identity", ())) for entry in data.get("entry", [])]

    def check_classified(self) -> None:
        selector = EdgeRule(self.ctx, self.spec)
        seen: dict[tuple[str, ...], tuple[EdgeEnv, Edge]] = {}
        for env in selector.envs():
            selected = selector.selection(env)
            if selected is Truth.FALSE:
                continue
            if env.dst.unresolved or selected is Truth.UNKNOWN:
                selector.on_unresolved(env) if env.dst.unresolved else selector.incomplete(
                    env, "selection reads an unknown value"
                )
                continue
            seen.setdefault(project(self.spec.identity, env.resolve), (env, env.edge))
        classified = set(self.classifications())
        for identity, (env, _) in sorted(seen.items()):
            if identity in classified:
                continue
            selector.emit(env, f"{' '.join(identity)} has no classification in {self.spec.classify}")
        for identity in sorted(classified - set(seen)):
            self.run.findings.append(
                Finding(
                    rule=self.spec.id,
                    identity=identity,
                    message=f"classification {' '.join(identity)} matches nothing (stale)",
                    site=None,
                    ledgerable=False,
                )
            )
        self.run.findings.extend(selector.run.findings)
        self.run.incomplete.extend(selector.run.incomplete)


class TurboInputs:
    """Which paths a turbo task watches: the nearest turbo.json with that task decides."""

    DEFAULT_MARKER = "$TURBO_DEFAULT$"

    def __init__(self, repo_root: Path, task: str) -> None:
        self.repo_root = repo_root
        self.task = task
        self.cache: dict[Path, tuple[str, ...] | None] = {}

    def inputs_for(self, directory: Path) -> tuple[str, ...] | None:
        if directory in self.cache:
            return self.cache[directory]
        result: tuple[str, ...] | None = None
        config = directory / "turbo.json"
        if config.is_file():
            task = json.loads(config.read_text()).get("tasks", {}).get(self.task)
            if isinstance(task, dict) and "inputs" in task:
                result = tuple(task["inputs"])
        self.cache[directory] = result
        return result

    def covered(self, path: str) -> tuple[bool, str]:
        """(covered, the turbo.json that decided). A package without a narrowed task covers all."""
        directory = (self.repo_root / path).parent
        while directory != self.repo_root and self.repo_root in directory.parents:
            inputs = self.inputs_for(directory)
            if inputs is not None:
                relative = str((self.repo_root / path).relative_to(directory))
                decided = str((directory / "turbo.json").relative_to(self.repo_root))
                if self.DEFAULT_MARKER in inputs:
                    return True, decided
                positive = [p for p in inputs if not p.startswith("!")]
                negative = [p[1:] for p in inputs if p.startswith("!")]
                hit = any(glob_match(p, relative) is not None for p in positive)
                miss = any(glob_match(p, relative) is not None for p in negative)
                return hit and not miss, decided
            directory = directory.parent
        return True, ""


class CoveragePrimitive(EntityRule):
    """Does an outside input list (turbo task inputs) cover every path the rule names?"""

    def required(self) -> dict[str, tuple[str, str | None]]:
        """path -> (why, module) for every path that must be covered."""
        out: dict[str, tuple[str, str | None]] = {}
        for cover in self.spec.covers:
            if cover.rule:
                self.from_rule(cover.rule, cover.column, out)
            else:
                self.from_edges(cover, out)
        return out

    def from_rule(self, rule_id: str, column: str, out: dict[str, tuple[str, str | None]]) -> None:
        run = self.ctx.results.get(rule_id)
        if run is None:
            self.incomplete(f"rules.{rule_id}", None, f"rule {rule_id} did not run before this coverage rule")
            return
        ref = Ref(tuple(column.split("."))) if "." in column else Ref(("edge", column))
        for finding in run.findings:
            for edge in finding.witnesses:
                src = self.ctx.src_endpoint(edge.site)
                env = EdgeEnv(self.ctx, edge, src, self.ctx.dst_endpoints(edge, "origin")[0])
                value = env.resolve(ref)
                if value.state is State.KNOWN:
                    out.setdefault(render_value(value), (f"rules.{rule_id}.{column}", None))

    def from_edges(self, cover: CoverSpec, out: dict[str, tuple[str, str | None]]) -> None:
        for edge in self.ctx.graph.edges:
            src = self.ctx.src_endpoint(edge.site)
            for dst in self.ctx.dst_endpoints(edge, "origin"):
                env = EdgeEnv(self.ctx, edge, src, dst)
                truth = cover.selector.evaluate(env.resolve) if cover.selector is not None else Truth.TRUE
                if truth is Truth.FALSE:
                    continue
                endpoint = src if cover.endpoint == "src" else dst
                if endpoint.unresolved or truth is Truth.UNKNOWN:
                    self.incomplete(
                        f"{src.module}|{edge.surface}", src.module, f"cannot place {edge.surface!r} for coverage"
                    )
                    continue
                if endpoint.path and endpoint.external is None:
                    out.setdefault(endpoint.path, (cover.text, endpoint.module))

    def expand(self, location: str) -> list[str]:
        if not location.endswith("/"):
            return [location]
        inside = sorted(p for p in self.ctx.graph.modules.values() if p.startswith(location))
        return inside or [location + "__init__.py"]

    def evaluate(self) -> RuleRun:
        repo = self.ctx.repo_root
        adapter = self.spec.input("adapter")
        if adapter == "turbo" and repo is None:
            self.incomplete("inputs", None, "no repo root to read turbo.json from")
            return self.run
        turbo = TurboInputs(repo, str(self.spec.input("task", ""))) if adapter == "turbo" and repo is not None else None
        globs = tuple(self.spec.input("paths", ()))
        for location, (why, module) in sorted(self.required().items()):
            for path in self.expand(location):
                if turbo is not None:
                    covered, decided = turbo.covered(path)
                else:
                    covered, decided = any(glob_match(g, path) is not None for g in globs), "inputs.paths"
                if covered:
                    continue
                owner_module = module or self.ctx.labeler.path_to_module.get(path)
                resolve = self.resolver(owner_module, None, path)
                self.emit(
                    path,
                    owner_module,
                    f"{path} is needed by {why} but {decided or 'the inputs'} does not watch it",
                    resolve,
                )
        return self.run
