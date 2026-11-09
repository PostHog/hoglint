from __future__ import annotations

import pytest

from hoglint.globs import GlobIndex, compile_glob, glob_match

PATTERNS = (
    "products/{product}/**",
    "products/{p}/backend/{p}.py",
    "{a}/{a}",
    "posthog/{infra}/**",
    "posthog/redis.py",
    "**/tests/**",
    "**/test_*.py",
    "**/conftest.py",
    "**/b.py",
    "a/**/b.py",
    "backend/logic.py",
    "backend/logic/**",
    "x?y/*.py",
    "/abs/{x}/*.py",
)
PATHS = (
    "b.py",
    "a/b.py",
    "a/x/y/b.py",
    "conftest.py",
    "x/conftest.py",
    "products/billing/backend/billing.py",
    "products/billing/backend/alerts.py",
    "products/billing/tests/test_api.py",
    "products/billing",
    "same/same",
    "same/other",
    "posthog/redis.py",
    "posthog/redis.pyc",
    "posthog/egress/client.py",
    "backend/logic.py",
    "backend/logic/rules.py",
    "backend/api/views.py",
    "xzy/f.py",
    "x/y/f.py",
    "/abs/q/z.py",
)


def regex_only(pattern: str, path: str) -> dict[str, str] | None:
    """The captures of the glob's regex alone, with no test on its literal ends."""
    match = compile_glob(pattern).match(path)
    if match is None:
        return None
    return {key: value for key, value in match.groupdict().items() if value is not None}


@pytest.mark.parametrize(
    "pattern, path, captures",
    [
        # a capture name used twice must match the same segment both times
        ("products/{p}/backend/{p}.py", "products/billing/backend/billing.py", {"p": "billing"}),
        ("products/{p}/backend/{p}.py", "products/billing/backend/alerts.py", None),
        ("{a}/{a}", "same/same", {"a": "same"}),
        ("{a}/{a}", "same/other", None),
        # `**/` may match nothing, so its slash is not part of the literal end
        ("**/b.py", "b.py", {}),
        ("a/**/b.py", "a/b.py", {}),
    ],
)
def test_label_globs_capture_like_their_regex(pattern: str, path: str, captures: dict[str, str] | None) -> None:
    assert glob_match(pattern, path) == captures


def test_literal_ends_and_the_glob_index_never_lose_a_match() -> None:
    index = GlobIndex((position, (pattern,)) for position, pattern in enumerate(PATTERNS))
    for path in PATHS:
        for pattern in PATTERNS:
            assert glob_match(pattern, path) == regex_only(pattern, path), (pattern, path)
        found = index.candidates(path)
        matching = [position for position, pattern in enumerate(PATTERNS) if regex_only(pattern, path) is not None]
        assert found == sorted(found)
        assert set(matching) <= set(found), path
    assert len(index.candidates("products/billing/backend/billing.py")) < len(PATTERNS)
