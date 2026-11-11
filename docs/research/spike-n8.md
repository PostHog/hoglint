# Spike N8: does a post-index-change background check hide the pre-commit wait?

> A small prototype with two git hooks in a scratch clone, Nov 10, 2026.
> Partly replaced: D59 item 1 did not pursue the prewarm hook (at most an opt-in extra); the fast-hook design is in `docs/design/08-fast-hook.md` and `docs/history/decision-log-3.md`.

**The question.**
A full check took about 3.5 to 5 s; a check of a tree whose result is already stored took about 0.55 s.
Git runs a `post-index-change` hook after the index is written (for example by `git add`).
If that hook starts the normal check for the staged content in the background, the later pre-commit check may find the stored result and return at once, with no engine change.
How often does that help in real flows (stage then commit, `commit -a`, many index changes, a rebase), and what does it cost?

**Short answer:** yes for "git add, pause, git commit" (wait 0.6-1.0 s instead of 4.4-6.5 s).
No for "add && commit" and `commit -a`.
Unguarded, it is a storm for many index changes (up to 20 checks at once, 17-20 GB, ~190 s CPU).

**Setup.**
A scratch clone of a staging repo, a fixed engine export, the native engine, one private cache directory, warmed once.
Two hooks: `post-index-change` starts the check detached in the background and returns at once; `pre-commit` runs the same check in the foreground and prints its wall time (it never blocks the commit).
The check is `python -m hoglint --engine native check --index` under `/usr/bin/time -l`.
Peak memory is the maximum over 0.15 s samples of the summed RSS of the spike's hoglint process trees (RSS double counts shared pages a little).
CPU is user+sys of all the spike's checks.
Measured under heavy load, so each run is listed.
Each flow ran three times, each with a new one-line edit to one file.

Wait = `git commit` wall (pre-commit check wall in brackets).
Control rows have no post-index-change hook.
Runs r1/r2/r3.

| flow | wait s (pre-commit s) | bg starts | max at once | peak MiB | CPU s |
|---|---|---|---|---|---|
| 1 hook (add, wait 6 s, commit) | 0.87 (0.62) / 0.82 (0.61) / 1.23 (0.72) | 3,3,3 | 2 | 2.5k | 10.9 / 11.1 / 12.8 |
| 1 control | 6.79 (6.47) / 4.60 (4.42) / 6.60 (6.15) | 0 | 1 | 1.25k | 5.0 / 4.4 / 4.8 |
| 2 hook (add && commit) | 5.20 (4.88) / 5.19 (4.99) / 10.21 (9.65) | 2,2,2 | 3 | 3.7k | 14.5 / 14.7 / 19.2 |
| 2 control | 4.81 (4.57) / 4.60 (4.43) / 12.31 (11.54) | 0 | 1 | 1.25k | 4.5 / 4.4 / 6.8 |
| 3 hook (`commit -a`) | 7.05 (6.66) / 4.79 (4.58) / 12.76 (12.21) | 1,1,1 | 2 | 1.26k | 5.6 / 5.0 / 7.5 |
| 3 control | 6.34 (6.01) / 4.54 (4.37) / 6.35 (6.04) | 0 | 1 | 1.26k | 4.6 / 4.3 / 5.8 |
| 4a ten adds + commit, hook | 26.6 / 17.5 / 21.9 | 20,19,19 | 21,20,20 | 17.4k / 19.8k / 18.2k | 199 / 185 / 196 |
| 4a control | 6.35 / 8.96 / 8.31 | 0 | 1 | 1.3-1.7k | 4.9 / 8.6 / 8.1 |
| 4b rebase of 10 commits, hook | 1.62 / 1.54 / 1.61 (rebase wall) | 14,14,14 | 14 | 17.0k / 17.7k / 15.1k | 115 / 127 / 136 |
| 4b control | 0.83 / 0.72 / 0.75 (rebase wall) | 0 | 0 | 0 | 0 |

r3 of flows 2 and 3 ran at the highest load, which explains their slow numbers in both arms.

**Guarded hook** (extra, 2 rounds): exit if `GIT_INDEX_FILE` is set; `mkdir` a lock directory or exit; the background job removes the lock when done.
Flow 1: 0.82 / 1.39 s.
Flow 2: 6.98 / 8.37 s (2 cold runs, 12 s CPU).
Flow 3: 8.0 / 8.3 s (no help).
4a: pre-commit 5.8 / 5.4 s, 1 bg, 2.5k MiB, ~11 s CPU.
4b: rebase 1.67 / 1.36 s, 1 bg, 1.65k MiB, 9-11 s CPU.

## Reading

**Helps: flow 1 only.**
The background run stores the result for the staged tree; the pre-commit check finds it (0.6-1.0 s here under load; the floor was ~0.55 s).
It saves 4-6 s per commit when the developer pauses 5 s or more after `git add`.
It does not help flow 2 (see below), flow 3, or any flow where the final index is written less than one check length before pre-commit.

**Flow 2: the pre-commit check neither waits nor reuses.**
hoglint writes a summary only at the end of a run and has no "run in progress" marker (the store's `get` reads finished files only; the only claim is the daily prune mark).
In the log, the add-hook background run starts at t+0.60 s, `git commit` itself fires post-index-change again (a second background run, t+0.80), and pre-commit starts at t+0.83: three cold runs of the same tree side by side, all ~4.9 s.
The wait is the same as the control; CPU 3x (14.5 s vs 4.5 s), memory 3x (3.7 GB).

**Flow 3:** `commit -a` fires the hook with the old real index, so the background run checks HEAD's tree (a stored hit, 0.7 s CPU, no value).
Pre-commit then runs on `index.lock` and is cold.
No help, small cost.

**The hook fires inside `git commit` itself:** yes (index refresh at commit start, before pre-commit), and for a rebase on each pick (args `1 0`).
Not at `commit` end.
`git commit` therefore always starts one extra background run that finds a stored result when the tree is warm, or races pre-commit when it is not.

**The storm (the real problem).**
hoglint itself runs `git write-tree` on a copy of the index.
That write fires post-index-change again, with `GIT_INDEX_FILE` pointing at hoglint's temp copy.
So each background run can spawn one more, and the extra run reads a temp index that is about to be deleted.
This showed in 4a (about two starts per `git add`: 19-20 for 10 adds) and in flow 1 (3 starts for add + commit).
The recursion stops after one level, because the copy's cache tree is then valid.
Unguarded, 4a ended with 20 checks at once, 17-20 GB, ~190 s CPU, and the pre-commit wait grew from 6-9 s to 17-27 s because it competed.
4b started 14 checks for 10 picks at once (15-18 GB, 115-136 s CPU); the rebase itself finished in 1.6 s, and the machine stayed busy for several more seconds afterwards.
Also, each hook call cost about 80 ms in the spike's script (two python3 starts for timestamps; a plain hook would cost less): the 4b rebase took 0.75 s without the hook and 1.6 s with it.

**Smallest guard** (a few lines of shell, tested above): `[ -n "$GIT_INDEX_FILE" ] && exit 0` (kills the recursion), plus `mkdir lock || exit 0` single-flight (skip while one runs).
Result: 1 background run for 4a and 4b, 2.5 GB and ~10 s CPU.
Weakness: the skipped later index states get no prewarm, so after a burst the pre-commit check runs cold beside the old background run (4a still waited 5.4-5.8 s).
"Cancel the older, start the newer" would fix this; it needs a process-group kill, not just shell lines, so it was not built.
hoglint as it stood could not tell a second process that a run for the same tree is under way.
A marker file keyed by the envelope would let pre-commit wait for it (flow 2) and let the hook skip an equal tree.
That is an engine change.

**Safety: a stale background run is not wrong.**
`check --index` writes the index tree once (`write_tree`), then reads code, policy and side inputs from that tree, and stores under the envelope of that tree.
A result is keyed by the tree it read, so a background run that finishes after the index moved on stores a correct result for an old tree (wasted CPU, never a wrong hit).
Concurrent writers of the same key use atomic rename.
Annoyances: the background process has no terminal output (redirected); the foreground check prints the whole 4.5k-line report on every commit (also on a hit); a real blocking hook would exit 1 (the spike's pre-commit always exits 0).
Not observed: a background run starting after its temp index copy was deleted (it would check the empty tree: harmless, but junk).

## Not measured, and limits

- Measured under heavy load; r3 numbers are noisy. No quiet-machine run.
- Memory is summed RSS, not footprint. No Linux run. One repo size only.
- Flow 4 with the guard: 2 rounds, not 3. The guarded flows ran at higher load.
- The "cancel older" guard was not built. Real developer behavior (how often 5 s or more pass between add and commit) was not measured.
