from __future__ import annotations

import shutil
import datetime
from pathlib import Path

import pytest

from hoglint.cli import COULD_NOT_RUN, HOOK_COULD_NOT_RUN, Commands, Run, Runner, Snapshots, main
from hoglint.config import Config, load_config
from hoglint.ledger.envelope import EngineError
from hoglint.ledger.listing import WorktreeStatus
from hoglint.model import SCHEMA_VERSION, Graph, Resolution
from hoglint.native import bridge
from hoglint.plugins import PLUGIN_ROOTS
from hoglint.report import View
from hoglint.source import GraphSource
from native.require import native_module

from rules.support import GraphBuilder, native_graph, write_config
from rules.test_compare import git_in

CONFIG = """
schema = 1
extends = ["hoglint:django"]

[fields]
package = { type = "id", required_on = ["products/**"] }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}" }

[rules.crossings]
primitive = "edge"
kinds = ["import"]
select = "src.package != dst.package"
identity = "module-pair"
growth_key = ["src.module"]

[rules.unresolved-anything]
primitive = "edge"
select = "edge.resolution == 'unresolved'"
ledger = false

[levels.isolated]
group_by = "package"
requires = ["crossings"]
"""


def graph() -> Graph:
    return (
        GraphBuilder()
        .module("products.billing.logic", "products.alerts.logic")
        .edge("import", "products.alerts.logic", "products.billing.logic")
        .graph
    )


@pytest.fixture
def commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Commands:
    built = graph()

    def build(self: GraphSource, code_root: Path, config: Config, staged: bool = False, tree: str = "") -> Graph:
        return native_graph(built)

    monkeypatch.setattr(GraphSource, "build", build)
    write_config(tmp_path / "repo", CONFIG)
    git_in(tmp_path / "repo", "init", "-q")
    (tmp_path / "work").mkdir()
    return Commands(tmp_path / "repo", tmp_path / "work")


def test_write_ledger_converges_in_one_run(commands: Commands) -> None:
    # the run that writes the ledger must already count the level lines that ledger makes pass
    assert commands.check(index=False, write_ledger=True, changed=False, record_growth=True) == 0
    assert commands.check(index=False, write_ledger=False, changed=False) == 0


@pytest.mark.parametrize(
    "ref, changed",
    [(None, ["dir x/b.py", "new file.py"]), ("HEAD", ["dir x/b.py"])],
    ids=["working tree", "against a ref"],
)
def test_changed_paths_keep_a_space_in_a_file_name(tmp_path: Path, ref: str | None, changed: list[str]) -> None:
    (tmp_path / "dir x").mkdir()
    (tmp_path / "dir x" / "b.py").write_text("b = 1\n")
    git_in(tmp_path, "init", "-q")
    git_in(tmp_path, "add", "-A")
    git_in(tmp_path, "commit", "-qm", "base")
    (tmp_path / "dir x" / "b.py").write_text("b = 2\n")
    (tmp_path / "new file.py").write_text("c = 1\n")
    assert sorted(Snapshots(tmp_path, tmp_path).changed_paths(ref)) == changed


class Panic(BaseException):
    """A stand-in for the exception PyO3 raises when Rust panics: like SystemExit, a BaseException."""


def missing_native_module() -> object:
    raise ModuleNotFoundError("No module named 'hoglint_native'")


@pytest.mark.parametrize(
    "argv, raised, reason",
    [
        (["explain-config"], KeyError("boom"), "hoglint could not run: KeyError: 'boom'. To repair"),
        (["explain-config"], Panic("called `Option::unwrap()` on a `None` value"), "could not run: Panic: called"),
        (
            ["explain-config"],
            EngineError("hoglint_native was built from other Rust sources"),
            "could not run: hoglint_native was built",
        ),
        (
            ["check", "--index"],
            None,
            "hoglint did not check this commit, because hoglint could not run: the native module hoglint_native is missing",
        ),
    ],
    ids=["crash", "rust panic", "stale build", "missing module in the hook"],
)
def test_a_run_that_could_not_finish_exits_3_with_one_line_never_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    raised: BaseException | None,
    reason: str,
) -> None:
    def crash(self: Commands, *args: object) -> int:
        if raised is None:
            bridge.load()
            return 0
        raise raised

    monkeypatch.setattr(Commands, "explain_config", crash)
    monkeypatch.setattr(Commands, "check", crash)
    monkeypatch.setattr(bridge, "import_module", missing_native_module)
    write_config(tmp_path / "repo", CONFIG)
    git_in(tmp_path / "repo", "init", "-q")
    assert main(["--repo", str(tmp_path / "repo"), *argv]) == 3
    printed = capsys.readouterr().err
    assert reason in printed and printed.count("\n") == 1, printed
    if isinstance(raised, KeyError):
        monkeypatch.setenv("HOGLINT_TRACEBACK", "1")
        assert main(["--repo", str(tmp_path / "repo"), *argv]) == 3
        assert "Traceback (most recent call last)" in capsys.readouterr().err


def test_the_hook_page_prints_with_no_repo_and_names_every_exit_and_the_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--repo", str(tmp_path / "nowhere"), "help", "hook"]) == 0
    page = capsys.readouterr().out
    assert [f"| {code} |" in page for code in range(COULD_NOT_RUN + 1)] == [True] * (COULD_NOT_RUN + 1)
    assert HOOK_COULD_NOT_RUN.format(reason="<reason>") in page
    assert "hoglint ledger shrink" in page and "hoglint explain <rule>" in page


def test_the_python_engine_is_refused_by_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        main(["--repo", str(tmp_path), "--engine", "python", "explain-config"])
    assert exited.value.code == 2
    assert "the python engine is gone" in capsys.readouterr().err


@pytest.mark.parametrize(
    "git_dir, error",
    [
        (None, "is not in a git working tree"),
        (".", "is not the top of its git working tree"),
    ],
    ids=["no git repo", "below the git top-level"],
)
def test_the_repo_root_is_the_top_of_a_git_working_tree_or_a_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], git_dir: str | None, error: str
) -> None:
    repo = tmp_path / "outer" / "sub"
    write_config(repo, CONFIG)
    if git_dir is not None:
        git_in(tmp_path / "outer", "init", "-q")
    assert main(["--repo", str(repo), "explain-config"]) == 2
    printed = capsys.readouterr().err
    assert printed.startswith(f"config error:\n  {repo.resolve()} {error}"), printed
    assert git_dir is None or str((tmp_path / "outer").resolve()) in printed


README_CONFIG = (
    CONFIG
    + """
[rules.readme]
primitive = "layout"
within = "products/{product}"
files = ["README.md"]
identity = "entity"
growth_key = ["subject"]
"""
)


def test_a_policy_root_apart_from_the_code_asks_about_the_code_roots_declared_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packages = GraphBuilder().package("products.billing", "products.alerts").graph

    def build(self: GraphSource, code_root: Path, config: Config, staged: bool = False, tree: str = "") -> Graph:
        return native_graph(packages)

    monkeypatch.setattr(GraphSource, "build", build)
    policy, code = tmp_path / "policy", tmp_path / "code"
    write_config(policy, README_CONFIG)
    write_config(code, CONFIG, {"products/billing/README.md": "billing\n"})
    git_in(code, "init", "-q")
    git_in(code, "add", "-A")
    (tmp_path / "work").mkdir()
    runner = Runner(GraphSource(tmp_path / "work"))
    run = runner.run(policy, code_root=code)
    _, evaluated = runner.evaluate(run.config, code, policy)
    for result in (run.result, evaluated):
        assert [finding.message for finding in result.runs["readme"].findings] == ["products/alerts lacks README.md"]
    # The policy root declares the README; the code root's own config does not.
    assert run.files_root is not None and PLUGIN_ROOTS.holds(run.files_root, "products/billing/README.md")
    with pytest.raises(EngineError, match="no source or declared input pattern covers it"):
        PLUGIN_ROOTS.holds(run.files_root, "products/billing/notes.txt")


def test_explain_edge_lists_rules_that_read_every_edge_kind(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    native_module()
    repo = tmp_path / "repo"
    write_config(repo, CONFIG)
    for path, text in {
        "products/__init__.py": "",
        "products/billing/__init__.py": "",
        "products/billing/logic.py": "def total():\n    return 1\n",
        "products/alerts/__init__.py": "",
        "products/alerts/logic.py": "from products.billing.logic import total\n",
    }.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git_in(repo, "init", "-q")
    git_in(repo, "add", ".")
    (tmp_path / "work").mkdir()
    commands = Commands(repo, tmp_path / "work", memo=False)
    run = commands.runner.run(repo)
    ordinals = run.result.labeler.index.ordinals
    edge = next(edge for edge in run.graph.edges if edge.kind == "import")
    assert commands.explain(f"edge:{edge.run_id(ordinals.get(edge.handle, 0))}") == 0
    out = capsys.readouterr().out
    assert "rule     unresolved-anything" in out
    assert "select: edge.resolution == 'unresolved' -> false" in out


MAP_CONFIG = """
schema = 1
extends = ["hoglint:django"]

[django]
settings = ["core.settings"]

[outputs.map]
path = "map.json"
"""
MAP_FILES = {
    "core/__init__.py": "",
    # Only the Django pack's path modules make this string an edge.
    "core/settings.py": 'LOGIC = "products.billing.logic"\n',
    "core/views.py": "from products.billing.logic import charge\n",
    "products/__init__.py": "",
    "products/billing/__init__.py": "",
    "products/billing/logic.py": "def charge():\n    return 1\n",
}


def test_map_prints_what_a_check_writes_for_a_tach_map_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    native_module()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    write_config(repo, MAP_CONFIG, MAP_FILES)
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    (tmp_path / "work").mkdir()
    Commands(repo, tmp_path / "work", memo=False).check(False, False, False)
    capsys.readouterr()
    assert Commands(repo, tmp_path / "work").map(None) == 0
    printed = capsys.readouterr().out
    assert printed == (repo / "map.json").read_text()
    assert '"core/settings.py"' in printed


CLAIMED_CONFIG = CONFIG.replace(
    'requires = ["crossings"]\n',
    'requires = ["crossings"]\nstrict = ["crossings"]\n'
    'claimed_by = { adapter = "globs", narrowed = ["products/alerts"] }\n',
)


def test_a_group_that_claims_a_level_it_fails_fails_check_whatever_the_ledger_live_and_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built = graph()

    def build(self: GraphSource, code_root: Path, config: Config, staged: bool = False, tree: str = "") -> Graph:
        return native_graph(built)

    monkeypatch.setattr(GraphSource, "build", build)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    write_config(repo, CLAIMED_CONFIG, {"products/alerts/logic.py": "", "products/billing/logic.py": ""})
    git_in(repo, "init", "-q")
    check_once(repo, tmp_path / "work", DAY, memo=False, staged=False, write_ledger=True)
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    capsys.readouterr()
    claims = []
    runs = count_runs(monkeypatch)
    # The second check reads the summary the first one stored.
    for memo in (True, True, False):
        assert check_once(repo, tmp_path / "work", DAY, memo=memo, staged=False) == 1
        printed = capsys.readouterr().out
        assert "new findings (0)" in printed
        claims.append(printed[printed.index("alerts claims the level") : printed.index("new findings (")])
    assert len(runs) == 2 and claims[0] == claims[1] == claims[2]
    printed = claims[0]
    assert printed.startswith("alerts claims the level isolated in inputs.narrowed, and fails it")
    assert "\n  crossings: strict, so the ledger does not excuse it: " in printed
    # billing fails the level too, but claims nothing
    assert "billing claims" not in printed
    assert "needs a person: To drop the narrowing in inputs.narrowed, stop and tell the person." in printed


MEMO_CONFIG = """
schema = 1

[fields]
package = { type = "id", required_on = ["products/**"] }
owners = { type = "set" }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}" }

[roles]
owner = "owners"

[providers.codeowners]
use = "codeowners"
file = "CODEOWNERS"
sets = ["owners"]

[ledger]
file = "local/hoglint.ledger"
exceptions = "local/hoglint.exceptions.toml"

[rules.crossings]
primitive = "edge"
kinds = ["import"]
select = "src.package != dst.package"
identity = "module-pair"
growth_key = ["src.module"]

[rules.notes]
primitive = "edge"
kinds = ["import"]
select = "src.package != dst.package"
identity = "module-pair"
report = "advisory"
ledger = false

[rules.dynamic-imports]
primitive = "edge"
kinds = ["dynamic-import"]
deny = "false"
on_unresolved = "incomplete"
ledger = false

[levels.isolated]
group_by = "package"
requires = ["crossings"]
"""
ACCEPTED_LINE = "crossings\tproducts.alerts.logic\tproducts.billing.logic\t1\n"
ALLOWED = """
[[exception]]
rule = "crossings"
match = { "src.module" = "products.alerts.views" }
reason = "moves behind the facade"
owners = ["team-alerts"]
scope = "prod"
review_by = 2026-06-01
"""
STALE = """
[[exception]]
rule = "crossings"
match = { "src.module" = "products.gone" }
reason = "removed"
owners = ["team-alerts"]
scope = "prod"
review_by = 2026-06-01
"""
DAY = datetime.date(2026, 5, 1)


def memo_graph(staged: bool, read_error: bool = False) -> Graph:
    graph = (
        GraphBuilder()
        .module("products.billing.logic", "products.alerts.logic", "products.alerts.views", "products.search.logic")
        .edge("import", "products.alerts.logic", "products.billing.logic")
        .edge("import", "products.alerts.views", "products.billing.logic")
        .edge("import", "products.search.logic", "products.billing.logic")
        .edge("dynamic-import", "products.search.logic", None, resolution=Resolution.UNRESOLVED)
    )
    # the staged code adds a crossing and a computed import, so a staged check differs from HEAD's
    if staged:
        graph.edge("import", "products.search.logic", "products.alerts.logic")
        graph.edge("dynamic-import", "products.alerts.logic", None, resolution=Resolution.UNRESOLVED)
    if read_error:
        unread = (("status", "read_error"),)
        graph.edge("unparsed", "products.search.logic", None, resolution=Resolution.UNRESOLVED, attrs=unread)
    return graph.graph


def memo_repo(root: Path) -> None:
    write_config(
        root,
        MEMO_CONFIG,
        {
            ".gitignore": "local/\n",
            "CODEOWNERS": "products/search/ @team-search\n",
            "products/search/logic.py": "",
            "local/hoglint.exceptions.toml": "schema = 1\n" + ALLOWED + STALE,
        },
    )
    header = f"# schema={SCHEMA_VERSION} policy={load_config(root, root / 'hoglint.toml').policy_hash}\n"
    (root / "local" / "hoglint.ledger").write_text(header + ACCEPTED_LINE + "crossings\tproducts.gone\tx\t1\n")
    git_in(root, "init", "-q")
    git_in(root, "add", "-A")
    git_in(root, "commit", "-qm", "base")


def count_runs(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    runs: list[Path] = []
    run = Runner.run

    def counted(self: Runner, policy_root: Path, *args: object, **kwargs: object) -> Run:
        runs.append(policy_root)
        return run(self, policy_root, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Runner, "run", counted)
    return runs


def check_once(
    repo: Path, work: Path, today: datetime.date, memo: bool, staged: bool, write_ledger: bool = False
) -> int:
    """One check, printing every row, so a test compares the whole output."""
    key = repo.resolve()
    WorktreeStatus.taken.pop(key, None)
    work.mkdir(exist_ok=True)
    commands = Commands(repo, work, today=today, memo=memo)
    try:
        return commands.check(staged, write_ledger, False, write_ledger, View("hoglint check", all=True))
    finally:
        commands.snapshots.close()


@pytest.mark.parametrize(
    "staged, change, ran_live, shows",
    [
        (True, None, False, None),
        (False, "source", True, None),
        # neither file is in the tree, so only the ledger source and the policy digest see an edit
        (False, "ledger", True, "crossings: products.alerts.logic imports products.billing.logic"),
        (False, "exceptions", True, "crossings: products.alerts.views imports products.billing.logic"),
        # a stored summary keeps review dates, so the day decides what is past review
        (False, "a day later", False, "is past its review date 2026-06-01"),
    ],
    ids=["staged", "source edited", "ledger edited", "exceptions edited", "a day later"],
)
def test_a_stored_summary_stands_in_for_a_check_of_its_tree_only_when_the_output_would_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    staged: bool,
    change: str | None,
    ran_live: bool,
    shows: str | None,
) -> None:
    def build(self: GraphSource, code_root: Path, config: Config, staged: bool = False, tree: str = "") -> Graph:
        return native_graph(memo_graph(staged))

    monkeypatch.setattr(GraphSource, "build", build)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    memo_repo(repo)
    if staged:
        (repo / "products/search/logic.py").write_text("X = 1\n")
        git_in(repo, "add", "-A")
    check_once(repo, tmp_path / "work", DAY, memo=True, staged=staged)
    first = capsys.readouterr().out
    today = DAY
    if change == "source":
        (repo / "products/search/logic.py").write_text("X = 2\n")
    if change == "ledger":
        ledger = repo / "local/hoglint.ledger"
        ledger.write_text(ledger.read_text().replace(ACCEPTED_LINE, ""))
    if change == "exceptions":
        (repo / "local/hoglint.exceptions.toml").write_text("schema = 1\n" + STALE)
    if change == "a day later":
        today = datetime.date(2026, 7, 1)

    runs = count_runs(monkeypatch)
    # the live run starts from the same cache, so it names its baseline the same way
    shutil.copytree(tmp_path / "cache", tmp_path / "live-cache")
    memoized = check_once(repo, tmp_path / "work", today, memo=True, staged=staged)
    printed = capsys.readouterr().out
    assert bool(runs) is ran_live
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "live-cache"))
    live = check_once(repo, tmp_path / "work", today, memo=False, staged=staged)
    assert (memoized, printed) == (live, capsys.readouterr().out)
    if shows is not None:
        assert shows in printed and shows not in first


def test_a_source_the_run_could_not_read_blocks_and_its_summary_is_never_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reads_fail = [True]

    def build(self: GraphSource, code_root: Path, config: Config, staged: bool = False, tree: str = "") -> Graph:
        return native_graph(memo_graph(staged, read_error=bool(reads_fail)))

    monkeypatch.setattr(GraphSource, "build", build)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    memo_repo(repo)
    assert check_once(repo, tmp_path / "work", DAY, memo=True, staged=False) == 2
    printed = capsys.readouterr().out
    assert "  source:unreadable [extractor, unreadable] products/search/logic.py could not be read" in printed
    assert "source:unreadable: this place has no facts, because hoglint cannot read the file.\n" in printed
    assert "  at products/search/logic.py: products/search/logic.py could not be read" in printed
    stored = tmp_path / "cache" / "hoglint" / "summaries"
    assert not list(stored.rglob("*.json"))
    reads_fail.clear()
    check_once(repo, tmp_path / "work", DAY, memo=True, staged=False)
    assert "unreadable" not in capsys.readouterr().out and list(stored.rglob("*.json"))
