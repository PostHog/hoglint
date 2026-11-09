"""`hoglint explain-config`: the expanded config, each key with the file that set it."""

from __future__ import annotations

import json
from typing import Any

from hoglint.config.model import Config


class ConfigExplainer:
    def __init__(self, config: Config) -> None:
        self.config = config

    def source(self, key: str) -> str:
        return self.config.provenance.get(key, "")

    def render(self) -> str:
        lines = [f"# expanded config, policy hash {self.config.policy_hash}"]
        for section in sorted(self.config.expanded):
            value = self.config.expanded[section]
            if section == "assign":
                lines.extend(self.render_assigns(value))
            elif isinstance(value, dict) and value and all(isinstance(v, dict) for v in value.values()):
                lines.extend(self.render_named(section, value))
            elif isinstance(value, dict):
                lines.extend(self.render_table(section, value))
            else:
                lines.append(f"{section} = {self.literal(value)}  # {self.source(section)}")
        lines.extend(self.render_domains())
        return "\n".join(lines) + "\n"

    def literal(self, value: Any) -> str:
        return json.dumps(value, default=str)

    def render_table(self, section: str, table: dict[str, Any]) -> list[str]:
        lines = ["", f"[{section}]"]
        for key, value in table.items():
            if isinstance(value, dict) and section == "zones":
                lines.append(f"{key} = {self.literal(value)}  # {self.source(f'{section}.{key}')}")
                continue
            lines.append(f"{key} = {self.literal(value)}  # {self.source(f'{section}.{key}')}")
        return lines

    def render_named(self, section: str, entries: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        for name, entry in entries.items():
            lines.append("")
            lines.append(f"[{section}.{name}]  # {self.source(f'{section}.{name}')}")
            for key, value in entry.items():
                lines.append(f"{key} = {self.literal(value)}  # {self.source(f'{section}.{name}.{key}')}")
        return lines

    def render_assigns(self, entries: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for index, entry in enumerate(entries):
            lines.append("")
            lines.append(f"[[assign]]  # #{index} from {entry.get('__source__', '?')}")
            for key, value in entry.items():
                if key != "__source__":
                    lines.append(f"{key} = {self.literal(value)}")
        return lines

    def render_domains(self) -> list[str]:
        lines = ["", "# field domains: outside a domain a missing value is a known none, inside it is unknown"]
        for spec in self.config.fields.values():
            domain = spec.domain
            if domain is None:
                text = "nowhere (missing always reads as none)"
            elif domain.everywhere:
                text = "everywhere"
            elif domain.selector is not None:
                text = domain.selector.source
            else:
                text = " | ".join(domain.globs)
            lines.append(f"# {spec.name}: {spec.type.value} on {spec.on}, required on {text}")
        lines.append("")
        lines.append("# ledgered rules: identity columns and growth key")
        for rule in self.config.rules.values():
            if not rule.ledger:
                continue
            identity = rule.identity_name or "custom"
            lines.append(
                f"# {rule.id}: identity {identity} ({', '.join(rule.identity)}), growth by ({', '.join(rule.growth_key)})"
            )
        return lines
