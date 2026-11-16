import pytest

from tools.lint.isolation import is_exempt


@pytest.mark.parametrize("package, expected", [("products.shelf", False), ("products.inventory", True)])
def test_is_exempt(package: str, expected: bool) -> None:
    assert is_exempt(package) is expected
