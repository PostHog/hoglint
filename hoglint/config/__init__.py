"""Policy config: load, merge, validate, explain."""

from __future__ import annotations

from pathlib import Path

from hoglint.config.loader import PACKS_DIR, ConfigError, ConfigLoader, MergedConfig
from hoglint.config.model import Config
from hoglint.config.schema import build_config

DEFAULT_CONFIG = "hoglint.toml"


def load_config(repo_root: Path, config_file: Path | None = None, packs_dir: Path = PACKS_DIR) -> Config:
    path = config_file if config_file is not None else repo_root / DEFAULT_CONFIG
    merged = ConfigLoader(repo_root, packs_dir).load(path)
    return build_config(merged)


__all__ = ["Config", "ConfigError", "ConfigLoader", "MergedConfig", "build_config", "load_config"]
