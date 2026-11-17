"""A rule's words as people and agents read them: the labels of the ways out, the lines under a
rule's first finding, and the page `hoglint explain <rule>` prints. The words come from the rule's
own table (`doc`, `why`, `ways`, `example`), their one source."""

from __future__ import annotations

from hoglint.config.model import Config, RuleSpec

SAFETY_LABELS = {"safe": "fix (safe)", "care": "fix (needs care)", "person": "needs a person", "note": "note"}


def way_lines(spec: RuleSpec) -> list[str]:
    """The cause and the ways out a rule prints under its first finding."""
    out = [f"why: {spec.why}"] if spec.why else []
    out.extend(f"{SAFETY_LABELS[way.safety]}: {way.text}" for way in spec.ways)
    return out


class RulePage:
    """What `hoglint explain <rule>` prints: what the rule is for, why a finding matters, the ways
    out, an example, and how the rule keeps and excuses its rows."""

    def __init__(self, spec: RuleSpec, config: Config) -> None:
        self.spec = spec
        self.config = config

    def kind(self) -> str:
        spec = self.spec
        ledger = "ledgered" if spec.ledger else "no ledger"
        return f"{spec.report}, {ledger}, {spec.primitive} rule"

    def levels(self) -> list[str]:
        out = []
        for level in self.config.levels.values():
            if self.spec.id in level.requires:
                strict = ", strict: the ledger does not excuse its rows" if self.spec.id in level.strict else ""
                out.append(f"level {level.name} requires it{strict}")
        return out

    def keeping(self) -> list[str]:
        spec = self.spec
        if not spec.ledger:
            return ["rows: a list; the ledger keeps none and no exception applies"]
        growth = f"; growth by {', '.join(spec.growth_key)}" if spec.growth_key else ""
        return [
            f"ledger line: {', '.join(spec.identity)}{growth}",
            f'exception: rule = "{spec.id}", match on {", ".join(spec.identity)}; it needs reason, '
            "owners, scope and review_by, and a person's review",
        ]

    def render(self) -> str:
        spec = self.spec
        out = [f"{spec.id}  ({self.kind()})", f"defined in {spec.source or '?'}"]
        if spec.doc:
            out.append(f"purpose: {spec.doc}")
        out.extend(way_lines(spec))
        if spec.example is not None:
            out.append("example:")
            out.append(f"  finding: {spec.example.finding}")
            out.append(f"  fixed:   {spec.example.fixed}")
        out.extend(self.levels())
        out.extend(self.keeping())
        return "\n".join(out) + "\n"
