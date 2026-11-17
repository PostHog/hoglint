# hoglint

hoglint is a boundary linter for large Python and Django monorepos, built at PostHog.

It reads every Python file of a repo and builds one graph of the couplings between its packages: imports, dotted paths in strings, Django model and settings references, and more.
It checks that graph against rules that you write in TOML.
Existing violations go into a generated ledger file, so a rule can block new problems on the first day, before the old ones are fixed.

A Rust engine extracts the facts and judges the rules.
A thin Python command line runs it.

## Install

```sh
uv add --dev hoglint
uv run hoglint check
```

The wheel holds the Python package, the native module and the fact extractor, so you do not need a Rust toolchain.
Wheels exist for Linux (x86_64 and aarch64, manylinux) and macOS (arm64 and x86_64).
hoglint needs Python 3.14 or newer.

## Configure

hoglint reads `hoglint.toml` at the root of the repo.
The config can extend the built-in Django pack (`extends = ["hoglint:django"]`) and presets of your own.
`hoglint explain-config` prints the expanded config, with the source of each key.

## Commands

| Command | What it does |
|---|---|
| `hoglint check` | Runs every rule on the working tree. |
| `hoglint check --index` | Checks the staged snapshot. This is what the pre-commit hook runs. |
| `hoglint check --write-ledger` | Rewrites the ledger from the scan. It adds new lines only with `--record-growth`. |
| `hoglint ledger shrink` | Removes stale ledger lines. It never adds a line. |
| `hoglint compare --base <ref>` | Checks a pull request against the merge base of `<ref>`. |
| `hoglint explain <rule>` | Prints the page of a rule. It also explains the labels of a path or module. |
| `hoglint query --by owners` | Lists open findings and accepted debt, grouped by owners or by target. |
| `hoglint map` | Prints the dependents map of the repo's Python files. It runs no rule. |
| `hoglint --version` | Prints the installed version. |

## Exit codes

| Exit | Meaning |
|---|---|
| 0 | Clean. |
| 1 | Violations: new findings, code growth, a ledger that is not equal to the scan, or a stale exception. |
| 2 | hoglint cannot decide: new code that it cannot judge, a base engine that cannot run, or a config error. |
| 3 | hoglint could not run. It prints one line that says why. |

## The pre-commit hook

`hoglint help hook` prints what the hook checks, what each exit code means for the commit, and what to do about each problem.

## Develop

To build from a checkout, see `AGENTS.md`: `bin/build` builds the extractor and the native module with the pinned Rust toolchain.
