"""One glob translator for every dialect hoglint reads.

`**` spans directories, `*` and `?` stay inside one segment. Braces differ by dialect: in label
assignments `{name}` captures one segment, in turbo inputs `{a,b}` picks one alternative, and in
CODEOWNERS they are plain characters.
"""

from __future__ import annotations

import re
from enum import StrEnum
from functools import cache
from typing import Any

CAPTURE_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class Braces(StrEnum):
    CAPTURE = "capture"
    ALTERNATION = "alternation"
    LITERAL = "literal"


def translate(pattern: str, braces: Braces) -> str:
    """The regex text of a glob, without anchors."""
    out: list[str] = []
    seen: set[str] = set()
    depth = 0
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
            continue
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
            continue
        if char == "{" and braces is Braces.CAPTURE:
            end = pattern.index("}", index)
            name = pattern[index + 1 : end]
            out.append(f"(?P={name})" if name in seen else f"(?P<{name}>[^/]+)")
            seen.add(name)
            index = end + 1
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "{" and braces is Braces.ALTERNATION:
            out.append("(?:")
            depth += 1
        elif char == "}" and depth:
            out.append(")")
            depth -= 1
        elif char == "," and depth:
            out.append("|")
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


@cache
def compile_glob(pattern: str, braces: Braces = Braces.CAPTURE) -> re.Pattern[str]:
    return re.compile(translate(pattern, braces) + r"\Z")


def glob_match(pattern: str, path: str) -> dict[str, str] | None:
    """The `{name}` captures when the path matches, else None."""
    match = compile_glob(pattern).match(path)
    if match is None:
        return None
    return {key: value for key, value in match.groupdict().items() if value is not None}


def fill(template: Any, captures: dict[str, str]) -> Any:
    """A `{name}` template with the captures of a match filled in; a non-string passes through."""
    if not isinstance(template, str):
        return template
    return CAPTURE_RE.sub(lambda m: captures.get(m.group(1), m.group(0)), template)
