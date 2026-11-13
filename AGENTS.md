# hoglint: notes for agents and engineers

hoglint is a boundary linter for large Python and Django monorepos, built at PostHog.
It extracts facts from every Python file, links them into one graph of couplings, and judges that graph against rules written in TOML, with existing debt carried in a generated ledger.
A Rust engine does the per-row work behind a thin Python shell (PyO3); it alone builds the graph.

## Read before you change the design

The design was worked out over a week of reviews, spikes and second opinions.
Most ideas that look obviously right were already weighed.

- `docs/design/README.md`: the current design, one page per topic, and how to use it.
- `docs/design/traps.md`: ideas that look right and were turned down.
- `docs/history/decision-log.md` (then `-2`, `-3`): the decisions D1 to D70 in order.
- `docs/research/README.md`: the studies, spikes and second opinions behind the decisions.

**Before you propose a design change:** read `docs/design/<topic>.md` and `docs/design/traps.md`.
In the proposal, say what was decided before and why, and what changed since (a new measurement, a changed condition, a new decision by the owner).

**When something is decided:** add it to the topic page (Decided, Why, Considered and rejected) and append an entry to the newest decision log file, with the next D-number, the date, and who decided.

## Fixed principles

These hold for every change.
A change that breaks one is a design change for the owner, not a refactor.

1. **Nothing deletes an edge.**
   Ledger lines and exceptions decide whether a finding blocks; they never remove an edge from reach, cycle or impact views.
2. **Unknown is never clean.**
   A parse failure, an unresolved name, an unsupported form or an exhausted budget is a stored obligation.
   An incomplete run exits 2, never 0.
   A column, key or file the engine cannot read is an error, not an empty answer.
3. **A cache problem is never a wrong answer and never a failed run.**
   It may only cost time.
   Keys come from the bytes actually read; a miss is never an error; a corrupt store is moved aside.
4. **Core holds no framework or company name.**
   Django lives in the Django pack, PostHog in its preset and plugin.
   The engine's word for a public surface is `export`, not "facade".
5. **One graph engine; a change moves no row it does not name.**
   The native engine builds the graph.
   The Python rule classes still judge rows again inside every native run, a second implementation of the rule half: a judgment that differs is an engine error.
   A change to rows is a named delta against the model repository's stored rows and tables, the slot table test and the real-size compare with a base.
6. **Every run is exact.**
   Any reuse of a stored answer must equal a full run, and an oracle must prove it.
7. **The seam rule.**
   Python hands Rust the config and the snapshot once and gets back rows it prints.
   A call per row, or an object per row that nobody prints, is the defect to look for.

## Build, test, lint

The Rust toolchain is pinned in `rust-toolchain.toml`; `bin/build` asks rustup for it.
Python tools come from the repo's own virtual environment at `.venv`, set up from the `dev` dependency group in `pyproject.toml`.
Call them by path, so no other environment is used by accident.

```sh
export CARGO_TARGET_DIR=<a target directory of your own>   # not a shared default
export XDG_CACHE_HOME=<a private cache directory>            # never your real hoglint cache
bin/build                                   # the extractor and the native module; rerun after any change under src/ or binding/
HOGLINT_REQUIRE_NATIVE=1 .venv/bin/python -m pytest -q   # a missing native module fails, never skips
cargo test                                  # the root package
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all --check
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/ty check --output-format concise  # some diagnostics exist on main; compare, and add none
bin/prove                                   # all of the above side by side, plus the model repository, determinism and store modes
```

- `bin/prove` builds first, runs every check above with a private copy of the native module and a private cache per check, and prints one line per check, with a log file each.
  With `--real <snapshot> --base <rev>` it also compares the head with the base row by row at real size; `--replay <sample>` adds the replay sample.
  `bin/prove --help` names the options and the environment variables for real-size runs.
- `cargo test --workspace` cannot link the binding's test binary; test the root package.
- `clippy` needs `--workspace`, or it never lints the binding crate.
- The PostHog owners provider imports a package that only the PostHog monorepo has, so two of its tests fail in this repo's environment until that provider moves out.
  Add no other failure.
- Parity dumps and build directories are large.
  Delete them after a compare, and keep each run's cache root private.

## How a change is verified

- **Zero delta against the base.**
  Run `bin/prove`, and `bin/prove --real <snapshot> --base <rev>` for a change that can move rows: the ten parity tables of the head against the base's.
  Zero delta is the default.
- **A named delta when results are meant to change.**
  List every changed row and explain it with your own script, not with the build's own summary.
  Compare row multisets, not identities: one identity can have several rows.
- **The model repository first.**
  After a rule or preset change, `bin/model-repo` shows in a second which cases changed verdict (`tests/model_repo/CASES.md`) and which of the ten tables moved (`tests/model_repo/tables.toml`).
  It does not replace the run at real size.
- **Same-minute pairs for speed.**
  Time base and new alternately, in the same minutes, CPU next to wall.
  A gap inside the spread is "not resolved".
  A number from a profile share or from reading code is an "estimate" until pairs confirm it.
- **Memory as physical footprint**, with RSS next to it.
- **An independent check of an agent's report.**
  Rerun its repros and recompute its key numbers before calling them verified.
  Several reports in this project were confidently wrong (a vacuous "equal", a wrong cause, a count from the wrong environment).
- **Determinism and portability** at the end of a phase: seeds and thread counts; macOS and Linux give the same keys and tables.

## Working rules

- Commit with `git commit -F <message file> -- <paths named by hand>`, after `git diff` on each path.
  Parallel agents use one git worktree each.
- Conventional commits; the body says why.
- No push, no remote, no publishing without the owner's explicit go.
- Code is boring and easy to read.
  A rule type is declared once, on its class; derive the schema, the plan and the generator from it.
