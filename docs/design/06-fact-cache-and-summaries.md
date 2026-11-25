# 6. The fact cache and stored run summaries

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 11, 2026; the pack memo entry as of Nov 16, 2026; the fact database's checksums and what may be copied to another machine as of Nov 25, 2026.

The fact cache, the run summaries and the pack memo are per machine, shared by all worktrees, under one cache root: `hoglint` under `XDG_CACHE_HOME`, which defaults to `.cache` in the home directory (D3, D10, D19).

## Decided

**Facts are keyed by (extractor build id, blob oid)** (architect, D4).
A snapshot is a list of (path, blob id): the index, a commit, or the working tree.
There are no checkouts.

**One fact database per facts id** (D19): SQLite in WAL mode, insert-or-ignore, shared by all worktrees.
- Each entry holds the record as JSON for the Python packs, compressed with lz4 one key at a time (since layout 4).
  It also holds a binary body for the link, uncompressed (D57 as it stands).
- Entry ids are never given out twice (layout 5, `AUTOINCREMENT`): a key whose rows were lost from outside is a miss, never another entry's facts. A write that breaks a constraint can only come from outside, so the database is moved aside like a corrupt one.
- Each body, and each frame of a record, has a checksum (layout 6): the first 8 bytes of the sha256 of the entry's extract config id, its oid and the bytes.
  A body that fails its checksum, or that does not decode, is a miss: the file is extracted again and its entry stored anew (principle 3, D28).
  A frame that fails its checksum is a record the database cannot give, so the file is extracted again and the record repaired.
  The decoder also refuses a count of items larger than the bytes left, so a damaged body cannot make it allocate without bound.
  A checksum finds damage, not a forged entry: whoever can write the file can write a matching checksum.
- Size for the PostHog monorepo: 321 MB at 461ef834c48 (Nov 11).
  It was 465-483 MB before phase N6.
- The bound is 1 GB.
  The prune keeps the current database and the one other opened most recently, removes empty directories, and never trims a row that a run read since the day before.

**One cached artifact type above facts: the run summary** (architect, D3; refined in D16 and D34).
- Key: (input set digest, engine id, policy hash, framework digest).
  The input set is (path, blob oid) over the discovered sources, the declared side inputs and the policy files.
  D34 replaced the whole-repo tree oid with this digest.
- Readers: the `check` baseline (new findings against inherited ones), the base and head of `compare`, the run memo, `query`, `explain`, and a summary published by CI.
- Only an exact key match counts.
  A corrupt or mismatched entry is a miss.
- A staged run reads every input from the index (D33).
  Only a tree run writes the store (D40).
  A working-tree snapshot is the index's blobs plus what `git status` reports; nothing else on disk is an input.
- A symlink inside the tree is read as its target (D41).
  An unreadable source always blocks, and a summary that holds one is never stored (D32 amended).
- Plugins declare the distributions they import.
  The framework digest covers the `.py` RECORD rows of those distributions on an intact install.
  Native extensions, package data and transitive imports are not covered.
  The supported contract is "declared distributions, intact installs" (D32, scoped down).
- The engine id is a digest of the engine sources, never of a platform binary.
  Paths come from git, never from the file system (D14, portability).
  The cross-platform gate passed at 461ef834c48 on macOS arm64, Linux arm64 and Linux amd64.

**The pack memo keeps each pack step's items with the reads they made** (stage S3 in `08-fast-hook.md`; `hoglint/core/memo.py`, `hoglint/native/memo_store.py`).
- One SQLite file per engine id and layout in `memo/` under the cache root; layout 5 since Nov 16.
- A later run reuses an item's output when every read the item made has the same answer again.
  That run may be on another tree, so a read names what it asked by values with one meaning in every tree: paths, module and symbol names, content keys.
- A lookup at a site (`value-lookup`, `chain-lookup`) names the site by its def index and line, which have a meaning only in one content of the file.
  So its key also holds the file's content key: where the file has other content, the read is a change and the link is not asked.
  The lookup also notes the file read, so an item that looked up a site of a file with no content key is not stored.
- A read that cannot be answered again (its file gone, a def index the file does not have, any error) is a change, and the item runs again: never a crash, never "unchanged" (architect, Nov 16).
  The link answers a def index that a file does not have with an error, not a panic.

**A consuming repo's main-branch CI can publish run summaries under the same key, from an existing CI job, with no new workflow** (owner, Nov 9; D14).
A PR's `compare` then restores the merge base's summary, and runs the full base scan on a miss.

**A missing baseline is computed inline; there is no background job** (architect, D39).
The target is Ruff-like speed from the tool itself, not a job that hides a second run (owner, Nov 10).

## What may be copied to another machine

A CI job may seed a run with a cache root that another machine filled.
Only `facts/` is a portable store.

| Under the cache root | Copy to another machine | Why |
|---|---|---|
| `facts/` | Yes | Data only. The file name holds the facts id and the table layout. A row's key holds the extract config id and the blob oid, or the sha256 of a third-party file's bytes, which mean the same on every machine and platform. A damaged row is a miss. Copy a database while no run has it open, or copy it with SQLite's backup (`VACUUM INTO`): a copy made during a write can be torn. |
| `memo/`, `summaries/`, `inputs/`, `packs/`, `answers/` | Not as a portable store | Data, and a damaged entry is a miss. But an entry holds what the run that wrote it declared it read, and a later run checks those reads, not the entry against the sources. The keys of `answers/site-packages` hold directory inodes and times, which seldom match on another machine. Run summaries go to other machines as `hoglint summary export` files (D14), not as a copy of `summaries/`. |
| `extractors/`, `engines/` | Never | Executable content. `extractors/` holds `hoglint-facts` binaries by native id, and a lookup runs the stored binary to read its id. `engines/` holds the Hoglint versions that base commits pin, and `compare` runs them. A file there runs with the rights of the user. Each machine fills them from its own installs. |

A checksum finds damage, not the writer.
A copied store is only as trustworthy as the run that wrote it.
Restore one only from a writer you trust, such as a main-branch CI run that no pull request can write to; otherwise run cold.

## Why

- Content keys: a cache hit reads no file, and staged mode reads the right policy.
- One summary type serves five readers, and an exact key cannot drift from a clean run.
- D28: an adversarial review showed that one editor save could poison a machine-wide fact entry for good.
- The checksums (layout 6): a store restored from a CI cache can hold a body that is damaged but passes the length check.
  Before them, a body that did not decode failed the run, and a body with one changed string decoded to the facts of other bytes.
  A record frame with one changed literal byte decompressed to its length, so a pack read wrong text.
- D33 and D34: staged runs read side inputs from the working tree, and one untracked file anywhere stopped the summary store, so misses were common.
  A miss cost 25.0 s and 2.33 GB; a hit cost 8.3 s and 1.40 GB (same minute, Nov 10).
- D40: a working-tree run stored under HEAD's key what `git status` cannot see (ignored `__pycache__`, sparse checkouts, skip-worktree bits).
- The JSON records stay: the binary body is a reduced projection for the link, not a copy.
  It lacks 66 % of the bytes the packs read.
- lz4 per key: zstd level 3 would give 63 MB for the records, at three times the decode time.
  lz4 is pure Rust and faster.
  Whole-record lz4 cost +0.045 s per warm check in the Django pack; per-key frames brought it back to the base.
- The content key in a lookup's read (layout 5): before it, the key held only the path, the def index and the line.
  A cache filled from one tree and then used for an older tree asked a stored def index of a file with fewer defs, and the run crashed.
  In the same pair, most stored lookups at a def index of a changed file still held: they answered about whatever def had that index now.
  Each of those items also held the read of its file, which had changed, so none was reused; the read alone did not say that.
- The 1 GB bound: the prune keeps two engine versions, and one version is 305-321 MB.
  So 500 MB could not hold them, and the size trim removed rows that the next check needs.

## Considered and rejected

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| About 29,000 small fact files | Replaced by one database | Architect, D19, Nov 9 | `docs/history/decision-log.md` |
| A mapped, position-independent fact store (D37) | Peak 1.25 to 1.11 GB for +1 s CPU per check; a full version about 3,000 lines for at most 0.24 GB | Architect withdrew it, D56 | `docs/research/spike-n5.md` |
| Drop the JSON records table ("one copy") | The premise was wrong: the body is a reduced projection; a new lossless format is several hundred lines in the gate path | Architect, Nov 10 | `docs/history/decision-log-3.md` |
| Compress the binary bodies with lz4 | 305 to 209 MB for +0.09-0.11 s CPU per warm check | Architect, Nov 10 | `docs/history/decision-log-3.md` |
| List site-packages from RECORD instead of a walk | A stale RECORD or an unlisted file differs silently; the walk stays (0.11 s) | Reverted in N6; architect | `docs/history/decision-log-3.md` |
| Never store dirty files | Hides the race instead of detecting it | Architect, D28 | `docs/history/decision-log-2.md` |
| Never store a working-tree run (a reviewer's smaller fix) | Leaves two meanings of "working tree" in two engines | Architect, D40 | `docs/history/decision-log-2.md` |
| Fetch main-branch summaries over the network locally | Costs about the same as computing; needs auth | Architect, D14 | `docs/history/decision-log.md` |
| A background warm from git hooks on a baseline miss | The target is Ruff-like speed, with no job (owner, Nov 10) | Architect, D39 | `docs/history/decision-log-2.md` |
| A stronger database (Redis-like) under Hoglint | Storage is about 0.1 s of a 3.75 s check | Brainstorm only, Nov 10 | `docs/history/decision-log-3.md` |

## What would change the answer

- Compressing the bodies comes back if a later design reads bodies for changed files only.
- A local fetch of summaries: revisit if a measured baseline in a fresh worktree takes more than about 5 s.
- One cache per machine on a busy laptop pays 1-2 s of disk reads when pages are evicted (15 of 26 base runs in the N5 spike).

## Open

- **The two cache trade-offs** (bodies uncompressed, the 1 GB bound): open since Nov 11.
  The bound is the owner's to change.
- **A mark for a read Python-engine namespace** is deferred until the next change that moves the facts id.
- No reason is recorded for keeping exactly two engine versions in the prune.
- A contradiction in the notes, settled: one log entry gives 305 MB (layout 3); the later measurement after layout 4 gives 321 MB.
  321 MB is current.

## Sources

`docs/history/decision-log.md` (D3, D4, D10, D14, D16, D19); `docs/history/decision-log-2.md` (D28, D32-D34, D37, D40, D41); `docs/history/decision-log-3.md` (D56, D57); `docs/research/spike-n5.md`; not published (the progress log).
