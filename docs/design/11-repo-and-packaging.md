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

**What lives where** (D15; "Every pack in the wheel", owner, Nov 17):
- The hoglint repo: all Rust (facts, link, evaluator, the PyO3 module), the generic core and every pack, each under `hoglint/packs/<name>/` with its `pack.toml` (Django; PostHog: `drives`, the owners provider, name lists).
  It ships one abi3 wheel.
- A consuming monorepo: its preset, the root `hoglint.toml` (which extends the packs it uses, such as `hoglint:posthog`), and the ledger and exceptions files.
  It pins the wheel and holds no hoglint Python.

**compare's base engine** (architect, Nov 17):
- When the base commit pins another hoglint version, `compare` installs that version without its dependencies (`[engine] install`, by default `uv pip install --no-deps --target`) into `engines/<version>` under the cache root.
  It runs that engine under the head's interpreter, so both engines read the same third-party sources.
- A base scan with a producer failure that the head scan does not have makes `compare` exit 2.
  Such a base is no reference for the head's unknowns.

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
- The base engine in the head's environment: hoglint reads third-party sources, such as Django's, from the interpreter's site-packages.
  A base scan in another environment is not comparable with the head scan.
- Prebuilt wheel: a Rust workspace member built locally conflicted with the dev environment's older cargo.
- The generic core and the Django pack become physically separate from PostHog's preset.
- Every pack in the wheel: packs no longer sit in two places with two load routes, the engine id and the base pin cover each pack's code, and a run needs no repo root on `sys.path`.

## Considered and rejected

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| A Rust crate inside the monorepo's `rust/` workspace | Toolchain conflict; coupled CI steps | Plan default; separate crate (owner, Nov 9) | `docs/research/adversarial-reviews.md` |
| Repack everything the source repo holds when extracting history | When extracting history from a partial clone, pack only the objects the new history reaches | Architect, Nov 11 | `docs/history/decision-log-3.md` |
| The PostHog pack as the monorepo's own plugin ("The PostHog pack's home", Nov 10) | Packs in two places, loaded two ways | Owner, Nov 17 | not published |
| The base engine run by `uv run --no-project --with hoglint==<base pin>` | That environment holds only hoglint, so the Django pack's producers fail on the base, and the head's unknowns look introduced | Architect, Nov 17 | not published |

## Open (the owner's calls)

- **Push, remote and package publishing** need the owner's explicit go.
- **History cleanup:** some replayed commits do not build alone, carry another phase's files, or have messages with numbers later corrected.
  Squashing the build-agent rounds is not decided; the architect's view is to keep them.
- Repo state after the move (Nov 11):
  - `requires-python` is 3.14 or newer, because `ledger/scan.py` and `store.py` import `compression.zstd`; CI tests 3.14 and 3.15;
  - `ty` reports a baseline of diagnostics on the whole tree; add none;
  - ruff's `src = ["src"]` is a workaround;
  - the PostHog owners provider imports a monorepo package, so two tests fail in the repo's own environment.
- The PostHog preset still sits in this repo, to move to the monorepo (D15); the PostHog pack stays (owner, Nov 17).

## Sources

`docs/history/decision-log.md` (D15); `docs/history/decision-log-2.md` (D32, "The PostHog pack's home"); `docs/history/decision-log-3.md` (D15 step 2, D68); `docs/research/adversarial-reviews.md`; not published (the history cleanup list, the build commands, the plan; "Every pack in the wheel", Nov 17).
