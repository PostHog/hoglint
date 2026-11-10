"""The `codeowners` provider: owners from a GitHub CODEOWNERS file, where the last matching line wins."""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from hoglint.globs import Braces, translate
from hoglint.plugins import REGISTRY


@cache
def codeowners_regex(pattern: str) -> re.Pattern[str]:
    """GitHub CODEOWNERS matching, which follows gitignore with one documented exception.

    A pattern with a slash at the start or in the middle is anchored at the repo root; otherwise
    it matches at any depth. A trailing slash matches only a directory's contents. A pattern that
    names a directory also owns everything below it, but a last segment of `*` matches only the
    files directly inside (`docs/*` owns `docs/a.md`, not `docs/sub/b.md`). `**` spans directories.
    """
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    directory = pattern.endswith("/")
    segments = pattern.strip("/").split("/")
    out = "" if anchored else "(?:.*/)?"
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            out += ".*" if last else "(?:.*/)?"
            continue
        out += translate(segment, Braces.LITERAL) + ("" if last else "/")
    if directory:
        out += "/.*"
    elif segments[-1] not in ("*", "**") or len(segments) == 1:
        out += "(?:/.*)?"
    return re.compile(out + r"\Z")


class CodeownersFile:
    def __init__(self, text: str) -> None:
        self.rules: list[tuple[str, frozenset[str]]] = []
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            pattern, *owners = line.split()
            self.rules.append((pattern, frozenset(owners)))

    def owners(self, path: str) -> frozenset[str] | None:
        found: frozenset[str] | None = None
        for pattern, owners in self.rules:
            if codeowners_regex(pattern).match(path):
                found = owners
        return found


class CodeownersProvider:
    use = "codeowners"
    allowed_options = frozenset({"file"})
    outputs = ("owners",)

    @staticmethod
    def file(options: Mapping[str, Any]) -> str:
        return str(options.get("file", ".github/CODEOWNERS"))

    def inputs(self, options: Mapping[str, Any]) -> tuple[str, ...]:
        return (self.file(options),)

    def open(self, options: Mapping[str, Any], root: Path) -> CodeownersFile:
        return CodeownersFile((root / self.file(options)).read_text())

    def values(self, handle: CodeownersFile, path: str) -> Mapping[str, Any]:
        owners = handle.owners(path)
        return {"owners": owners} if owners else {}


REGISTRY.add_provider(CodeownersProvider())
