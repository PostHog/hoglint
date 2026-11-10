"""Errors that end a run with exit 2 and a one-line reason, not a traceback."""

from __future__ import annotations


class EngineError(Exception):
    """A run cannot go on: an engine a comparison needs cannot run as pinned, or git cannot give
    an input the run reads."""
