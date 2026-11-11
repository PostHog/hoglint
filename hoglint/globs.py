"""One glob translator for every dialect hoglint reads.

`**` spans directories, `*` and `?` stay inside one segment. Braces differ by dialect: in label
assignments `{name}` captures one segment, in turbo inputs `{a,b}` picks one alternative, and in
CODEOWNERS they are plain characters.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from operator import itemgetter
from typing import Any, Generic, TypeVar

CAPTURE_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
T = TypeVar("T")


class Braces(StrEnum):
    CAPTURE = "capture"
    ALTERNATION = "alternation"
    LITERAL = "literal"


@dataclass(frozen=True, slots=True)
class Token:
    """One piece of a glob: its regex text and where it sits in the pattern. A literal token is
    one character that matches only itself."""

    regex: str
    start: int
    end: int
    literal: bool


def tokens(pattern: str, braces: Braces) -> list[Token]:
    out: list[Token] = []
    seen: set[str] = set()
    depth = 0
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            out.append(Token("(?:.*/)?", index, index + 3, False))
            index += 3
            continue
        if pattern.startswith("**", index):
            out.append(Token(".*", index, index + 2, False))
            index += 2
            continue
        if char == "{" and braces is Braces.CAPTURE:
            end = pattern.index("}", index)
            name = pattern[index + 1 : end]
            out.append(Token(f"(?P={name})" if name in seen else f"(?P<{name}>[^/]+)", index, end + 1, False))
            seen.add(name)
            index = end + 1
            continue
        if char == "*":
            out.append(Token("[^/]*", index, index + 1, False))
        elif char == "?":
            out.append(Token("[^/]", index, index + 1, False))
        elif char == "{" and braces is Braces.ALTERNATION:
            out.append(Token("(?:", index, index + 1, False))
            depth += 1
        elif char == "}" and depth:
            out.append(Token(")", index, index + 1, False))
            depth -= 1
        elif char == "," and depth:
            out.append(Token("|", index, index + 1, False))
        else:
            out.append(Token(re.escape(char), index, index + 1, True))
        index += 1
    return out


def translate(pattern: str, braces: Braces) -> str:
    """The regex text of a glob, without anchors."""
    return "".join(token.regex for token in tokens(pattern, braces))


def literal_ends(pattern: str, braces: Braces) -> tuple[str, str]:
    """The text every match of the glob starts with and ends with: the pattern before its first
    token that is not a literal character, and after its last."""
    special = [token for token in tokens(pattern, braces) if not token.literal]
    if not special:
        return pattern, pattern
    return pattern[: special[0].start], pattern[special[-1].end :]


@cache
def compile_glob(pattern: str, braces: Braces = Braces.CAPTURE) -> re.Pattern[str]:
    return re.compile(translate(pattern, braces) + r"\Z")


@dataclass(frozen=True, slots=True)
class Glob:
    """A compiled label glob. Every match starts with `prefix` and ends with `suffix`, so two
    string tests turn most paths away before the regex runs."""

    prefix: str
    suffix: str
    regex: re.Pattern[str]

    @property
    def directory(self) -> str:
        """The deepest directory every match lies in, with its trailing slash; the root is ''."""
        head, slash, _ = self.prefix.rpartition("/")
        return head + slash

    def match(self, path: str) -> dict[str, str] | None:
        """The `{name}` captures when the path matches, else None."""
        if not path.startswith(self.prefix) or not path.endswith(self.suffix):
            return None
        found = self.regex.match(path)
        if found is None:
            return None
        return {key: value for key, value in found.groupdict().items() if value is not None}


@cache
def compile_label_glob(pattern: str) -> Glob:
    prefix, suffix = literal_ends(pattern, Braces.CAPTURE)
    return Glob(prefix, suffix, compile_glob(pattern))


def glob_match(pattern: str, path: str) -> dict[str, str] | None:
    """The `{name}` captures when the path matches, else None."""
    return compile_label_glob(pattern).match(path)


class GlobIndex(Generic[T]):
    """Items that each carry label globs, looked up by path. A glob is filed under the directory
    its prefix spells out, and a path gets the items filed under one of its own directories (the
    root included), in the order the items were given. No other glob could match the path."""

    def __init__(self, items: Iterable[tuple[T, Iterable[str]]]) -> None:
        self.filed: dict[str, list[tuple[int, T]]] = {}
        self.found: dict[str, list[T]] = {}
        for position, (item, patterns) in enumerate(items):
            for directory in {compile_label_glob(pattern).directory for pattern in patterns}:
                self.filed.setdefault(directory, []).append((position, item))

    def collect(self, directory: str) -> list[T]:
        pairs = list(self.filed.get("", []))
        prefix = ""
        for part in directory.split("/") if directory else ():
            prefix += part + "/"
            pairs.extend(self.filed.get(prefix, []))
        return list(dict(sorted(pairs, key=itemgetter(0))).values())

    def candidates(self, path: str) -> list[T]:
        directory = path.rpartition("/")[0]
        found = self.found.get(directory)
        if found is None:
            found = self.found[directory] = self.collect(directory)
        return found


def fill(template: Any, captures: dict[str, str]) -> Any:
    """A `{name}` template with the captures of a match filled in; a non-string passes through."""
    if not isinstance(template, str):
        return template
    return CAPTURE_RE.sub(lambda m: captures.get(m.group(1), m.group(0)), template)


@dataclass(frozen=True, slots=True)
class ExportPaths:
    """The files an export covers: its `paths`, read relative to the directory `within` names
    (`within` may capture `{name}` parts; empty means the repo root)."""

    within: str
    paths: tuple[str, ...]

    def covered_root(self, path: str) -> str | None:
        """The directory `within` names for a path the export covers, its captures filled in
        (`products/billing`); None for a path it does not cover."""
        root, relative = "", path
        if self.within:
            captures = glob_match(self.within + "/**", path)
            if captures is None:
                return None
            root = fill(self.within, captures)
            relative = path[len(root) + 1 :]
        if not any(glob_match(pattern, relative) is not None for pattern in self.paths):
            return None
        return root

    def covers(self, path: str) -> bool:
        return self.covered_root(path) is not None
