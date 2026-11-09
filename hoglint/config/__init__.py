"""Policy config: load, merge, validate, explain."""

from __future__ import annotations

from pathlib import Path

from hoglint.config.loader import PACK_PREFIX, PACKS_DIR, ConfigError, ConfigLoader, MergedConfig
from hoglint.config.model import Config
from hoglint.config.schema import as_tuple, build_config
from hoglint.plugins import PACK_MODULE_PREFIX, import_plugins

CONFIG_FILE = "hoglint.toml"


def plugin_modules(merged: MergedConfig) -> list[str]:
    """Modules that register this config's plugins: each extended pack, then `[discovery] plugins`."""
    packs = [
        PACK_MODULE_PREFIX + label.removeprefix(PACK_PREFIX) for label in merged.files if label.startswith(PACK_PREFIX)
    ]
    return [*packs, *as_tuple(merged.data.get("discovery", {}).get("plugins"))]


def load_config(repo_root: Path, config_file: Path | None = None, packs_dir: Path = PACKS_DIR) -> Config:
    path = config_file if config_file is not None else repo_root / CONFIG_FILE
    merged = ConfigLoader(repo_root, packs_dir).load(path)
    problems = import_plugins(plugin_modules(merged))
    if problems:
        raise ConfigError(problems)
    return build_config(merged)


__all__ = ["CONFIG_FILE", "Config", "ConfigError", "ConfigLoader", "MergedConfig", "build_config", "load_config"]
