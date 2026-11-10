"""How many threads a staged run takes (D6 revised)."""

from __future__ import annotations

import pytest

from hoglint.native.analysis import staged_threads


@pytest.mark.parametrize(
    ("cores", "load", "threads"),
    [
        (16, 0.3, 4),  # an idle machine: at most four
        (16, 13.6, 2),  # a partial core left over is not a thread
        (16, 15.5, 1),  # never fewer than one
        (16, 40.0, 1),  # and never zero, which the link reads as every core
        (2, 0.0, 2),
    ],
)
def test_a_staged_run_takes_the_idle_cores_up_to_four(cores: int, load: float, threads: int) -> None:
    assert staged_threads(cores, load) == threads
