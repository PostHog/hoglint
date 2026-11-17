"""Plugins that ship with hoglint: coverage adapters and label providers. Importing this package
registers them."""

from hoglint.adapters import codeowners, globs, provenance, turbo, yaml_key

__all__ = ["codeowners", "globs", "provenance", "turbo", "yaml_key"]
