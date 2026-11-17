"""Errors that end a run with a one-line reason, not a traceback."""

from __future__ import annotations


class EngineError(Exception):
    """hoglint cannot run: its native module or extractor is missing or stale, the engine failed,
    or git cannot give an input the run reads. The command exits 3. `compare` reports a base
    engine that cannot run as its own blocking result (exit 2) instead."""


class RepoRootError(Exception):
    """The repo root is not the top of a git working tree. The CLI reports it as a config error;
    it lives here because the ledger cannot import the config package."""

    def __init__(self, problem: str) -> None:
        self.problems = [problem]
        super().__init__(problem)
