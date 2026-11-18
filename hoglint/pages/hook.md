# The hoglint hook

The pre-commit hook runs `hoglint check --index` before each commit.
It checks the staged tree: the code, `hoglint.toml`, the preset, `hoglint.ledger` and `hoglint.exceptions.toml`, as you will commit them.
It compares the result with HEAD.
An old problem does not block.
A new problem blocks.
A finding or a ledger line that HEAD has already, out of step with the ledger, is an old problem: the output names them in one line, and CI judges them.
This is so only when the commit does not change the policy: `hoglint.toml`, the files it extends, `hoglint.exceptions.toml` and the data files it names.
A commit that finishes a merge compares the result with each parent: what any parent has is an old problem.

## What the hook checks

- New findings of a blocking rule.
- The ledger: it must equal the scan.
- Ledger lines that this commit adds when the policy does not change. A line of a finding that only moved is no new debt. CI judges a move the same way.
- The exceptions: each exception must be complete and must match a finding.
- A product that claims the Isolated level must pass it. A product claims the level where the config's `claimed_by` says, such as narrowed contract-check inputs or a key in its manifest.
- New code that hoglint cannot judge (a new unknown).
- Source files that hoglint cannot read.

## Exit codes

| Exit | Meaning | The commit |
|---|---|---|
| 0 | No new problem. | Continues. |
| 1 | A new problem. The output names each problem and what to do. | Stops. |
| 2 | hoglint cannot decide: new code that it cannot judge, a config error, or a file that it cannot read. | Stops. |
| 3 | hoglint could not run. It prints one line. | Continues. CI runs the full check. |

## What to do

| Problem | Do this | Safety |
|---|---|---|
| A new finding | Do the fix that the output gives. `hoglint explain <rule>` gives more. | As the output says. |
| No fix fits | Stop and tell the person. | Needs a person. |
| Stale ledger lines | Run `hoglint ledger shrink`, then stage `hoglint.ledger`. The command only removes lines. | Safe. |
| Findings that moved | Run `hoglint check --write-ledger`, then stage `hoglint.ledger`. The command records the new places and refuses new debt. | Safe. |
| The commit adds ledger lines | Remove them. Fix the findings, or stop and tell the person. | Needs a person. |
| A problem in `hoglint.exceptions.toml` | Stop and tell the person. A person writes and reviews exceptions. | Needs a person. |
| A product fails the level that it claims | Do the fixes that the output gives. If the claim is narrowed inputs, such as a turbo.json, and a reason names a file that they do not watch, add the file to the watched inputs. If the claim is a key, such as `isolated: true`, hoglint derives the watched files, so fix each reason in the code. To remove the claim, stop and tell the person: the product is then no longer isolated. | Safe, needs care, or needs a person. |
| New code that hoglint cannot judge | Use a literal name. Else stop and tell the person. | Safe, or needs a person. |
| A config error | The output names the file and the line. If you did not change the file, stop and tell the person. | Needs a person. |

## Do not do these things to make the check pass

- Do not edit `hoglint.ledger` by hand. Do not record new lines with `--write-ledger --record-growth`.
- Do not add an exception.
- Do not change `hoglint.toml`, the preset or the hoglint version.
- Do not use `git commit --no-verify`. CI runs the same check, and more.

## When the hook lets a commit through

If hoglint cannot run at all, it exits 3 and prints one line, and the hook lets the commit continue.
This happens when the native module is missing or was built from other sources, when the engine stops with an error or a panic, or when the hook cannot find hoglint.
A wrong block costs more than a miss, and CI holds the full check.
The line is:

    hoglint did not check this commit, because hoglint could not run: <reason>. The commit continues. CI runs the full check. To repair hoglint, see `hoglint help hook`.

To repair hoglint, install the pinned hoglint version again. In a hoglint checkout, run `bin/build`.
Set `HOGLINT_TRACEBACK=1` to see the traceback of a crash.

A problem with the cache does not stop a run.
hoglint runs without the cache and prints one line.

Any real answer blocks: a finding, a config error, an unknown in new code, and a file that hoglint cannot read.

## The first check on a machine

The first check with a new hoglint version fills the cache.
It is slower than the next checks, and it says so in one line:

    hoglint: the pack memo is empty, so this check fills it. This check is slower than the next ones.

## Where the full check runs

CI runs `hoglint compare --base <merge base>` as a required check.
It does what the hook does, against the base instead of HEAD: what the base has already does not fail the change, and `hoglint check` of the base reports it.
It also:

- It blocks new debt that the code adds against the merge base.
- It lets new debt through when a complete exception in the change allows it. A complete exception has a reason, owners, a scope, a review date and a match. It lists that debt under "needs a person's review". The owners of the exceptions file review it.
- It labels each change to `hoglint.toml`, the preset or the exceptions as tighten or relax. A relaxation needs the review of the owners.
- It blocks when the engine of the base commit cannot run.

## More output

- `hoglint check --index --all` prints every row: each finding, each advisory row and each old unknown.
- `hoglint check --index --rule <rule>` prints the rows of one rule.
- `hoglint explain <rule>` prints the purpose of a rule, its fixes and an example.
- `hoglint compare --base <ref> --all` prints every section of the comparison.
