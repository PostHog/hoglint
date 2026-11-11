# 11. The own repo and packaging

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 11, 2026. Adoption in the PostHog monorepo (cutover order, CI wiring, PR plan) is not published and is not part of this page.

## Decided

**hoglint gets its own repo** (owner's proposal, Nov 9; D15).
- Timing (owner, Nov 9): first stabilize the split between Rust and Python and the pack API, then move.
- The owner confirmed the move on Nov 10.
- Done on Nov 11 (D15 step 2): 256 commits replayed from the monorepo folder that held hoglint.
  Each kept the old commit's tree for the folder, its author, dates and message.
  The head tree is equal to the old folder's tree.
  One commit was skipped, because it changed only the monorepo's root config.

**What lives where** (D15; "The PostHog pack's home"):
- The hoglint repo: all Rust (facts, link, evaluator, the PyO3 module), the generic core and the Django pack.
  It ships one abi3 wheel.
- A consuming monorepo: its preset, its own plugin (for PostHog: `drives`, the owners provider, name lists), the root `hoglint.toml`, the ledger and exceptions files, and the parity and shadow tooling.
  It pins the wheel.

**Packaging** (a separate crate; owner, Nov 9):
- A prebuilt, pinned wheel.
  Developers need no Rust compiler.
- Release wheels are built in a manylinux container, from tags only.
  Local builds use `--compatibility linux` (D32, "Local builds").
- The toolchain is pinned by `rust-toolchain.toml`.
  Older cargo versions are too old for the ruff crates hoglint uses.

## Why

- Own repo: frequent coordinated source changes between the tool and the monorepo do not happen.
  hoglint can iterate, release and build on its own, and the monorepo pins a version.
  `compare` can then run its base engine from the base pin.
- Prebuilt wheel: a Rust workspace member built locally conflicted with the dev environment's older cargo.
- The generic core and the Django pack become physically separate from PostHog's preset.

## Considered and rejected

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| A Rust crate inside the monorepo's `rust/` workspace | Toolchain conflict; coupled CI steps | Plan default; separate crate (owner, Nov 9) | `docs/research/adversarial-reviews.md` |
| Repack everything the source repo holds when extracting history | When extracting history from a partial clone, pack only the objects the new history reaches | Architect, Nov 11 | `docs/history/decision-log-3.md` |

## Open (the owner's calls)

- **Push, remote and package publishing** need the owner's explicit go.
- **History cleanup:** some replayed commits do not build alone, carry another phase's files, or have messages with numbers later corrected.
  Squashing the build-agent rounds is not decided; the architect's view is to keep them.
- Repo state after the move (Nov 11):
  - `requires-python` says 3.12, but `ledger/scan.py` and `store.py` import `compression.zstd` (Python 3.14);
  - `ty` reports a baseline of diagnostics on the whole tree; add none;
  - ruff's `src = ["src"]` is a workaround;
  - the PostHog owners provider imports a monorepo package, so two tests fail in the repo's own environment until that provider moves out.
- The PostHog preset and pack still sit in this repo, to move to the monorepo (D15).

## Sources

`docs/history/decision-log.md` (D15); `docs/history/decision-log-2.md` (D32, "The PostHog pack's home"); `docs/history/decision-log-3.md` (D15 step 2, D68); `docs/research/adversarial-reviews.md`; not published (the history cleanup list, the build commands, the plan).
