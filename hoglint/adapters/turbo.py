"""Which repo paths a turbo task watches, read the way turbo reads its config.

- A workspace package runs the task when its package.json has a script of that name. The repo
  root runs it only through a root task named `//#<task>`.
- A package's task config is its own turbo.json entry. With `extends: ["//"]` it inherits the
  root entry key by key; `$TURBO_EXTENDS$` in `inputs` appends to the root's inputs instead of
  replacing them. Without a package entry the root's `<package name>#<task>` or `<task>` applies.
- No `inputs` key means `$TURBO_DEFAULT$`: every git-tracked file inside the package.
- Input globs are relative to the package and may climb out with `../`; `$TURBO_ROOT$/` makes a
  glob relative to the repo root; a leading `!` excludes from that package's inputs.

A path is watched when some package that runs the task includes it and does not exclude it.

Coverage asks a different question: does a change to the path reach the task? A path in a package
that has no script for the task, or outside every package, is covered: CI runs the full suite for
any change there. A path is not covered only when its package runs the task and its inputs leave
the path out.
"""

from __future__ import annotations

import re
import json
import posixpath
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from hoglint.globs import Braces, compile_glob
from hoglint.ledger.cache import TURBO_ANSWERS, AnswerStore
from hoglint.plugins import PLUGIN_ROOTS, REGISTRY

DEFAULT_MARKER = "$TURBO_DEFAULT$"
EXTENDS_MARKER = "$TURBO_EXTENDS$"
ROOT_MARKER = "$TURBO_ROOT$/"
ROOT_PACKAGE = "//"


class TurboError(ValueError):
    """A turbo.json or package.json that hoglint cannot read."""


def strip_jsonc(text: str) -> str:
    """turbo.json is JSON with comments and trailing commas; drop both outside strings."""
    out: list[str] = []
    index = 0
    in_string = False
    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\":
                out.append(text[index + 1 : index + 2])
                index += 2
                continue
            in_string = char != '"'
            index += 1
            continue
        if char == '"':
            in_string = True
        elif text.startswith("//", index):
            index = text.find("\n", index)
            index = len(text) if index == -1 else index
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
            continue
        out.append(char)
        index += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


@dataclass(frozen=True, slots=True)
class PackageTask:
    """One package that runs the task, with its inputs made relative to the repo root."""

    directory: str
    decided_by: str
    includes: tuple[str, ...]
    excludes: tuple[str, ...]
    default: bool


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(strip_jsonc(path.read_text()))
    except (OSError, ValueError) as error:
        raise TurboError(f"{path.name} in {path.parent}: {error}") from error
    if not isinstance(data, dict):
        raise TurboError(f"{path}: expected a JSON object")
    return data


@dataclass(frozen=True, slots=True)
class TaskTable:
    """What the turbo config says about one task: the workspace package directories, sorted, and
    the packages that run the task. It depends only on the turbo.json and package.json files."""

    directories: tuple[str, ...]
    tasks: tuple[PackageTask, ...]

    def to_json(self) -> dict[str, Any]:
        rows = [[t.directory, t.decided_by, list(t.includes), list(t.excludes), t.default] for t in self.tasks]
        return {"directories": list(self.directories), "tasks": rows}

    @staticmethod
    def from_json(data: Any) -> TaskTable | None:
        """None for data of any other shape."""
        try:
            directories = tuple(data["directories"])
            tasks = tuple(
                PackageTask(directory, decided, tuple(includes), tuple(excludes), default)
                for directory, decided, includes, excludes, default in data["tasks"]
            )
        except (KeyError, TypeError, ValueError):
            return None
        texts = [*directories, *(text for task in tasks for text in (task.directory, task.decided_by))]
        texts += [glob for task in tasks for glob in (*task.includes, *task.excludes)]
        if not all(isinstance(text, str) for text in texts):
            return None
        if not all(isinstance(task.default, bool) for task in tasks):
            return None
        return TaskTable(directories, tasks)


class TurboReader:
    """Reads one task's `TaskTable` from the turbo.json and package.json files under a root."""

    def __init__(self, repo_root: Path, task: str) -> None:
        self.repo_root = repo_root
        self.task = task
        self.tree = PLUGIN_ROOTS.tree(repo_root)
        self.root_config = self.read_config(repo_root / "turbo.json")

    def read(self) -> TaskTable:
        directories = self.package_dirs()
        return TaskTable(tuple(directories), tuple(self.package_tasks(directories)))

    def read_config(self, path: Path) -> dict[str, Any]:
        return read_json(path) if path.is_file() else {}

    def git(self, *args: str) -> list[str] | None:
        """git output lines, or None when the root is not a git work tree: a root written from a
        git tree, which holds only the declared files."""
        if self.tree is not None:
            return None
        completed = subprocess.run(["git", "-C", str(self.repo_root), *args], capture_output=True, text=True)
        if completed.returncode != 0:
            return None
        return completed.stdout.splitlines()

    def package_dirs(self) -> list[str]:
        listed = self.git("ls-files", "--", "**/package.json")
        if listed is None:
            listed = [
                str(path.relative_to(self.repo_root))
                for path in self.repo_root.rglob("package.json")
                if "node_modules" not in path.parts
            ]
        return sorted(posixpath.dirname(path) for path in listed if posixpath.dirname(path))

    def root_task(self, key: str) -> dict[str, Any] | None:
        task = self.root_config.get("tasks", {}).get(key)
        return task if isinstance(task, dict) else None

    def task_config(self, directory: str, name: str) -> tuple[dict[str, Any], str] | None:
        """The merged task config for a package, and the turbo.json that set its inputs."""
        root = self.root_task(f"{name}#{self.task}") or self.root_task(self.task)
        package_file = self.repo_root / directory / "turbo.json"
        package = self.read_config(package_file)
        own = package.get("tasks", {}).get(self.task)
        if not isinstance(own, dict):
            return (dict(root), "turbo.json") if root is not None else None
        relative_file = f"{directory}/turbo.json"
        if ROOT_PACKAGE not in package.get("extends", []) or root is None:
            return dict(own), relative_file
        merged = {**root, **own}
        inputs = own.get("inputs")
        if isinstance(inputs, list) and EXTENDS_MARKER in inputs:
            extra = [item for item in inputs if item != EXTENDS_MARKER]
            merged["inputs"] = [*root.get("inputs", [DEFAULT_MARKER]), *extra]
        decided = relative_file if "inputs" in own else "turbo.json"
        return merged, decided

    def package_task(self, directory: str, manifest: dict[str, Any]) -> PackageTask | None:
        scripts = manifest.get("scripts", {})
        if not isinstance(scripts, dict) or self.task not in scripts:
            return None
        found = self.task_config(directory, str(manifest.get("name", directory)))
        if found is None:
            return None
        config, decided = found
        inputs = config.get("inputs", [DEFAULT_MARKER])
        if not isinstance(inputs, list) or not all(isinstance(item, str) for item in inputs):
            raise TurboError(f"{decided}: tasks.{self.task}.inputs must be a list of strings")
        includes = [
            self.anchor(directory, item) for item in inputs if item != DEFAULT_MARKER and not item.startswith("!")
        ]
        excludes = [self.anchor(directory, item[1:]) for item in inputs if item.startswith("!")]
        return PackageTask(directory, decided, tuple(includes), tuple(excludes), DEFAULT_MARKER in inputs)

    def root_package_task(self) -> PackageTask | None:
        config = self.root_task(f"{ROOT_PACKAGE}#{self.task}")
        if config is None:
            return None
        inputs = config.get("inputs", [DEFAULT_MARKER])
        includes = [self.anchor("", item) for item in inputs if item != DEFAULT_MARKER and not item.startswith("!")]
        excludes = [self.anchor("", item[1:]) for item in inputs if item.startswith("!")]
        return PackageTask("", "turbo.json", tuple(includes), tuple(excludes), DEFAULT_MARKER in inputs)

    def package_tasks(self, directories: list[str]) -> list[PackageTask]:
        out: list[PackageTask] = []
        for directory in directories:
            task = self.package_task(directory, read_json(self.repo_root / directory / "package.json"))
            if task is not None:
                out.append(task)
        root = self.root_package_task()
        if root is not None:
            out.append(root)
        return out

    @staticmethod
    def anchor(directory: str, glob: str) -> str:
        if glob.startswith(ROOT_MARKER):
            return glob.removeprefix(ROOT_MARKER)
        return posixpath.normpath(posixpath.join(directory, glob))


class TurboInputs:
    """Answers whether the task's inputs watch a path. `table` is the task's table when it is known
    already; else it is read from the files under `repo_root`."""

    def __init__(self, repo_root: Path, task: str, table: TaskTable | None = None) -> None:
        self.repo_root = repo_root
        self.task = task
        self.table = TurboReader(repo_root, task).read() if table is None else table
        self.directory_set = frozenset(self.table.directories)
        self.task_by_directory = {task.directory: task for task in self.table.tasks}
        self.globs = {
            task.directory: (
                [compile_glob(glob, Braces.ALTERNATION) for glob in task.excludes],
                [compile_glob(glob, Braces.ALTERNATION) for glob in task.includes],
            )
            for task in self.table.tasks
        }
        self.answers: dict[str, tuple[bool, str]] = {}

    def tracked(self, path: str) -> bool:
        """Whether git tracks the path: whether the run's snapshot holds it. A working tree run
        holds the files git status reports as untracked too, as the commit would (D40)."""
        return PLUGIN_ROOTS.holds(self.repo_root, path)

    def watches(self, task: PackageTask, path: str) -> bool:
        excludes, includes = self.globs[task.directory]
        if any(pattern.match(path) for pattern in excludes):
            return False
        if any(pattern.match(path) for pattern in includes):
            return True
        inside = not task.directory or path.startswith(task.directory + "/")
        return task.default and inside and self.tracked(path)

    def holder(self, path: str) -> str:
        """The innermost workspace package directory that holds the path; "" for the repo root."""
        parts = path.split("/")[:-1]
        for end in range(len(parts), 0, -1):
            directory = "/".join(parts[:end])
            if directory in self.directory_set:
                return directory
        return ""

    def covered(self, path: str) -> tuple[bool, str]:
        """(covered, why): the turbo.json that decided, or why a change there runs everything
        anyway. Answered once per path."""
        found = self.answers.get(path)
        if found is None:
            found = self.answers[path] = self.find_covered(path)
        return found

    def find_covered(self, path: str) -> tuple[bool, str]:
        for task in self.table.tasks:
            if self.watches(task, path):
                return True, task.decided_by
        holder = self.holder(path)
        task = self.task_by_directory.get(holder)
        if task is None:
            where = f"{holder}/package.json" if holder else "the repo root"
            return True, f"{where} runs no {self.task}, so any change there runs the full suite"
        return False, task.decided_by


@cache
def turbo_inputs(repo_root: Path, task: str) -> TurboInputs:
    return TurboInputs(repo_root, task)


class TurboAdapter:
    """`inputs = { adapter = "turbo", task = "..." }`: the task's inputs, read from turbo.json."""

    name = "turbo"
    allowed_options = frozenset({"task"})
    STORE = AnswerStore(TURBO_ANSWERS, __file__)

    def inputs(self, options: Mapping[str, Any]) -> tuple[str, ...]:
        return ("**/turbo.json", "**/package.json")

    def open(self, options: Mapping[str, Any], root: Path) -> TurboInputs:
        return turbo_inputs(root.resolve(), str(options.get("task", "")))

    def open_run(self, options: Mapping[str, Any], run_root: Path) -> TurboInputs:
        """`open` for a run whose root is `run_root`. The task table depends only on the files
        this adapter declares and on this module, so it is stored under their blob ids and read
        back with no file written or parsed."""
        inputs = PLUGIN_ROOTS.key(run_root, self, options)
        if not inputs:
            return self.open(options, PLUGIN_ROOTS.of(run_root, self, options))
        task = str(options.get("task", ""))
        key = self.STORE.key(task, inputs)
        table = TaskTable.from_json(self.STORE.get(key))
        if table is not None:
            return TurboInputs(run_root.resolve(), task, table)
        handle = self.open(options, PLUGIN_ROOTS.of(run_root, self, options))
        self.STORE.put(key, handle.table.to_json())
        return handle

    def covered(self, handle: TurboInputs, path: str) -> tuple[bool, str]:
        return handle.covered(path)


REGISTRY.add_coverage_adapter(TurboAdapter())
