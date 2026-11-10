"""Errors that end a run with exit 2 and a one-line reason, not a traceback."""

from __future__ import annotations


class EngineError(Exception):
    """A run cannot go on: an engine a comparison needs cannot run as pinned, or git cannot give
    an input the run reads."""


class RepoRootError(Exception):
    """The repo root is not the top of a git working tree. The CLI reports it as a config error;
    it lives here because the ledger cannot import the config package."""

    def __init__(self, problem: str) -> None:
        self.problems = [problem]
        super().__init__(problem)
