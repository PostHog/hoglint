"""Parity of the ported spike L2 against today's tools on the current tree.

Usage: python -m hoglint.spike.parity <repo_root> <work_dir> <tach_map.json>
"""

from __future__ import annotations

import sys
import json
import time
from pathlib import Path

from hoglint.facts import ExtractOptions, Extractor
from hoglint.spike import drives, facade, import_map, migrations, relations, uses
from hoglint.spike.core import Core, FactStore

EXTERNAL_PACKAGES = (
    "axes",
    "corsheaders",
    "django",
    "django_admin_inline_paginator",
    "django_filters",
    "django_linear_migrations",
    "django_otp",
    "django_structlog",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "loginas",
    "oauth2_provider",
    "rest_framework",
    "social_django",
    "social_core",
    "two_factor",
    "whitenoise",
)
FIRST_PARTY = ("posthog", "ee", "products", "common")


def kind_of(line: str) -> str:
    return line.split()[2].split("(")[0]


def main() -> None:
    repo, work, tach_path = Path(sys.argv[1]).resolve(), Path(sys.argv[2]), Path(sys.argv[3])
    timings: dict[str, float] = {}
    start = time.perf_counter()
    facts = Extractor().run(ExtractOptions(repo, FIRST_PARTY, EXTERNAL_PACKAGES, work / "facts.jsonl"))
    timings["extract"] = time.perf_counter() - start

    start = time.perf_counter()
    store = FactStore()
    store.load(facts)
    core = Core(store)
    timings["load"] = time.perf_counter() - start

    walked = [p for p in (work / "facts.files").read_text().splitlines() if p and "site-packages/" not in p]
    start = time.perf_counter()
    tach = import_map.run(core, walked)
    rel = relations.run(core)
    graph = migrations.run(core, repo / "products/db_routing.yaml")
    use = uses.run(core)
    drv = drives.run(core)
    base = [
        line
        for line in (repo / "products/model_crossing_uses_baseline.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    wiring_products = frozenset(line.split(".")[0] for line in base if " facade-wiring " in line)
    fac = facade.run(core, wiring_products)
    timings["l2"] = time.perf_counter() - start

    ledger = sorted(set(rel["ledger"]) | set(use["ledger"]) | set(drv["ledger"]) | set(fac["ledger"]))
    oracle = json.loads(tach_path.read_text())
    te = {(i, t) for t, v in oracle.items() for i in v}
    me = {(i, t) for t, v in tach["map"].items() for i in v}
    per: dict[str, list[int]] = {}
    for line in base:
        per.setdefault(kind_of(line), [0, 0])[0] += 1
    for line in set(base) & set(ledger):
        per[kind_of(line)][1] += 1
    summary = {
        "tach_map": {
            "oracle": len(te),
            "mine": len(me),
            "missing": len(te - me),
            "extra": len(me - te),
            "missing_sample": sorted(te - me)[:15],
            "extra_sample": sorted(me - te)[:15],
        },
        "ledger": {
            "baseline": len(base),
            "derived": len(ledger),
            "equal": len(set(base) & set(ledger)),
            "baseline_only": sorted(set(base) - set(ledger))[:40],
            "derived_only": sorted(set(ledger) - set(base))[:40],
            "per_kind_baseline_equal": per,
        },
        "migrations": {k: len(graph[k]) for k in ("nodes", "edges")},
        "timings_s": {k: round(v, 2) for k, v in timings.items()},
    }
    (work / "parity.json").write_text(json.dumps(summary, indent=1))
    sys.stdout.write(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()
