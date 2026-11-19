//! One small Python snippet per fact kind, with the rows and `cfg` ids it must give.

use crate::{ExtractConfig, FileRecord, Kind, Kinds, Status, extract, record_line, sha256_hex};

fn strings(items: &[&str]) -> Vec<String> {
    items.iter().map(|item| item.to_string()).collect()
}

/// `--first-party app --watch-callees import_module,get_model --watch-prefixes register_
/// --watch-keys kind`.
fn config() -> ExtractConfig {
    ExtractConfig::new(strings(&["app"])).watching(
        strings(&["import_module", "get_model"]),
        strings(&["register_"]),
        strings(&["kind"]),
    )
}

fn record_with(raw: &[u8], config: &ExtractConfig) -> FileRecord {
    extract(raw, sha256_hex(raw), config)
}

fn record(raw: &[u8]) -> FileRecord {
    record_with(raw, &config())
}

fn line_with(source: &str, config: &ExtractConfig) -> serde_json::Value {
    let record = record_with(source.as_bytes(), config);
    let line = record_line(&record, &config.config_id(), "f.py").unwrap();
    serde_json::from_str(&line).unwrap()
}

/// The JSON of one fact list of the record for `source`, or "" when the list is empty.
fn facts(source: &str, kind: &str) -> String {
    let value = line_with(source, &config());
    value.get(kind).map(|v| v.to_string()).unwrap_or_default()
}

/// The fact list keys of a record line.
fn kinds_of(value: &serde_json::Value) -> Vec<String> {
    let envelope = ["bytes", "cfg", "path", "sha256", "status", "v"];
    let keys = value.as_object().unwrap().keys();
    keys.filter(|key| !envelope.contains(&key.as_str()))
        .cloned()
        .collect()
}

#[test]
fn imports_carry_flags_and_relative_levels() {
    let source = "\
import os.path
from app.models import Thing as T
from . import sibling
if TYPE_CHECKING:
    from app import Other
else:
    import json
";
    assert_eq!(
        facts(source, "imports"),
        r#"[[-1,1,7,0,"os.path",0,null,null],[-1,2,23,0,"app.models",0,"Thing","T"],[-1,3,14,0,null,1,"sibling",null],[-1,5,20,1,"app",0,"Other",null],[-1,7,11,2,"json",0,null,null]]"#
    );
}

#[test]
fn defs_encode_headers_and_none_annotations() {
    let source = r#"@register(1, name="x")
async def fetch(a: int, /, b=None, *rest, c: "app.Thing" = 2, **kw) -> None:
    """Doc."""
    ...

class Box(Base, metaclass=Meta):
    def size(self) -> int | None:
        return 1
"#;
    assert_eq!(
        facts(source, "defs"),
        r#"[{"c":0,"dec":[{"args":[1],"call":"register","kw":[["name",{"s":"x"}]]}],"e":4,"k":"async","l":2,"n":"fetch","params":[["a","po","int"],["b","a"],["rest","va"],["c","ko",{"s":"app.Thing","sa":"app.Thing"},2],["kw","kw"]],"ret":"None","s":-1,"stub":true},{"bases":["Base"],"c":0,"e":8,"k":"class","kw":[["metaclass","Meta"]],"l":6,"n":"Box","s":-1},{"c":4,"e":8,"f":8,"k":"def","l":7,"n":"size","params":[["self","a"]],"ret":{"bin":"|","l":"int","r":"None"},"s":1}]"#
    );
}

#[test]
fn methods_record_what_they_assign_to_their_first_parameter() {
    let source = r#"class Menu:
    family: Family

    def __init__(self, kind):
        self.family = FAMILIES[kind]
        self.size: int = 3
        self.a, other = pair()
        if kind:
            self.count += 1

        def later():
            self.late = Late()

        def own(self):
            self.own = Own()

    @staticmethod
    def build(kind):
        kind.x = 1

def outside(self):
    self.free = 1
"#;
    let defs: Vec<String> = line_with(source, &config())["defs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|d| format!("{} {}", d["n"], d["attrs"]))
        .collect();
    assert_eq!(
        defs,
        [
            r#""Menu" null"#,
            r#""__init__" [[5,"family",{"i":"kind","sub":"FAMILIES"}],[6,"size",3,"int"],[7,"a",{"o":"unspelled"}],[9,"count",{"o":"unspelled"}],[12,"late",{"o":"unspelled"}]]"#,
            r#""later" null"#,
            r#""own" null"#,
            r#""build" [[19,"x",1]]"#,
            r#""outside" null"#,
        ]
    );
}

#[test]
fn assigns_cover_module_and_class_scope_only() {
    let source = r#"LIMIT: int = 10
ITEMS = [a, b] + EXTRA
NOTHING = None
try:
    COUNT += 1
except ImportError:
    pass

class Config:
    name = "box"

def local():
    hidden = 1
"#;
    assert_eq!(
        facts(source, "assigns"),
        r#"[{"ann":"int","c":0,"l":1,"op":":","s":-1,"t":["LIMIT"],"v":10},{"c":0,"l":2,"op":"=","s":-1,"t":["ITEMS"],"v":{"bin":"+","l":{"list":["a","b"]},"r":"EXTRA"}},{"c":0,"l":3,"op":"=","s":-1,"t":["NOTHING"]},{"c":4,"f":16,"l":5,"op":"+=","s":-1,"t":["COUNT"],"v":1},{"c":4,"f":8,"l":10,"op":"=","s":0,"t":["name"],"v":{"s":"box"}}]"#
    );
}

#[test]
fn refs_and_derived_locals_carry_their_statement_flags() {
    let source = "\
from app.models import Thing
from app import models

def view():
    x = Thing.objects
    y = x.filter()
    return y.first(), models.Other, z

z = 1

if TYPE_CHECKING:
    Thing.check()
";
    assert_eq!(
        facts(source, "refs"),
        r#"[[0,-1,5,8,4,"Thing",".objects","assign:x"],[0,-1,7,22,4,"models",".Other","elt:tuple return"],[-1,-1,12,4,1,"Thing",".check()","expr"]]"#
    );
    assert_eq!(
        facts(source, "derived"),
        r#"[[0,-1,6,8,4,"x",".filter()","assign:y",0],[0,-1,7,11,4,"y",".first()","elt:tuple return",0]]"#
    );
}

#[test]
fn derived_locals_ignore_source_order() {
    let source = "\
from app.models import Thing

def view():
    def inner():
        return later.go()
    later = first.all()
    first = Thing.objects
";
    assert_eq!(
        facts(source, "derived"),
        r#"[[1,-1,5,15,4,"later",".go()","return",0],[0,-1,6,12,4,"first",".all()","assign:later",0]]"#
    );
}

#[test]
fn calls_keep_watched_callees_with_shallow_arguments() {
    let source = r#"import importlib

def load():
    try:
        importlib.import_module("app.plugins", package=None)
    except ImportError:
        pass
    apps.get_model("app", "Thing")
    receiver([post_save, post_delete], sender=Thing)
    register_hook((a.b, c), [1, x])
"#;
    assert_eq!(
        facts(source, "calls"),
        r#"[[0,-1,5,8,20,"importlib.import_module",[{"s":"app.plugins"}],[["package",null]]],[0,-1,8,4,4,"apps.get_model",[{"s":"app"},{"s":"Thing"}],[]],[0,-1,10,4,4,"register_hook",[{"tuple":["a.b","c"]},{"o":"List"}],[]]]"#
    );
    let unwatched = ExtractConfig::new(strings(&["app"]));
    assert!(line_with(source, &unwatched).get("calls").is_none());
}

#[test]
fn a_hook_call_keeps_its_arguments_through_an_import_alias_and_a_call_argument_keeps_its_callee() {
    let source = r#"from app.hooks import add_hook as on_ready, add_hook
from app.other import add_hook as unrelated

def wire():
    on_ready(handler)
    add_hook(Provider(), key="x")
    unrelated(other)
    apps.get_model(Thing())
"#;
    let hooks = config().watching_hooks(strings(&["add_hook"]));
    assert_eq!(
        line_with(source, &hooks)["calls"].to_string(),
        r#"[[0,-1,5,4,4,"on_ready",["handler"],[]],[0,-1,6,4,4,"add_hook",[{"call":"Provider","o":"Call"}],[["key",{"s":"x"}]]],[0,-1,7,4,4,"unrelated",["other"],[]],[0,-1,8,4,4,"apps.get_model",[{"o":"Call"}],[]]]"#
    );
    // Without the hook watch, neither alias is a watched callee.
    assert_eq!(
        line_with(source, &config())["calls"]
            .as_array()
            .map(Vec::len),
        Some(1)
    );
    // The option is part of the record's cfg only when it is set.
    assert_ne!(hooks.config_id(), config().config_id());
}

#[test]
fn a_loop_over_a_literal_sequence_gives_one_call_row_per_value() {
    let source = r#"LABELS = ("app.Thing", "app.Other")
GROWN = ["app.Thing"]
GROWN.append("app.More")
TWICE = ("app.Thing",)
TWICE = ("app.Other",)

def wire():
    local = ["app.Local"]
    for label in LABELS:
        apps.get_model(label)
        register_later(lambda: apps.get_model(label))
    for label in local:
        apps.get_model(label)
    for label in ("app.Inline",):
        for kind in (a.b, c):
            register_kind(label, kind=kind)
    for label in GROWN:
        apps.get_model(label)
    for label in TWICE:
        apps.get_model(label)
    for label in LABELS:
        label = label.lower()
        apps.get_model(label)
"#;
    assert_eq!(
        facts(source, "calls"),
        r#"[[0,-1,10,8,68,"apps.get_model",[{"s":"app.Other"}],[]],[0,-1,10,8,68,"apps.get_model",[{"s":"app.Thing"}],[]],[0,-1,11,8,68,"register_later",[{"o":"Lambda"}],[]],[0,-1,11,31,68,"apps.get_model",["label"],[]],[0,-1,13,8,68,"apps.get_model",[{"s":"app.Local"}],[]],[0,-1,16,12,68,"register_kind",[{"s":"app.Inline"}],[["kind","a.b"]]],[0,-1,16,12,68,"register_kind",[{"s":"app.Inline"}],[["kind","c"]]],[0,-1,18,8,68,"apps.get_model",["label"],[]],[0,-1,20,8,68,"apps.get_model",["label"],[]],[0,-1,23,8,68,"apps.get_model",["label"],[]]]"#
    );
}

#[test]
fn strs_tags_and_names_read_string_literals() {
    let source = r#"PATH = "app.models.Thing"
PARTS = ("app.views." "Home")
LABEL = "Thing"
NOTE = "see <Widget> and <Box"
if TYPE_CHECKING:
    TARGET = "app.typing.Alias"
"#;
    assert_eq!(
        facts(source, "strs"),
        r#"[[-1,-1,1,7,0,"app.models.Thing","","assign:PATH"],[-1,-1,2,9,0,"app.views.Home","","assign:PARTS",[null,"Home"]],[-1,-1,6,13,1,"app.typing.Alias","","assign:TARGET"]]"#
    );
    assert_eq!(
        facts(source, "tags"),
        r#"[[-1,-1,4,7,0,"Box"],[-1,-1,4,7,0,"Widget"]]"#
    );
    assert_eq!(facts(source, "names"), r#"[[-1,-1,"Thing",1,3,8]]"#);
}

#[test]
fn uses_and_callees_count_per_scope() {
    let source = r#"import os
from django import forms

def f():
    os.path.join("a", "b")
    os.getcwd()
    forms.Form()
    return "Widget", "Widget"
"#;
    assert_eq!(
        facts(source, "uses"),
        r#"[[0,-1,"os",".path.join",1,5,4],[0,-1,"os",".getcwd",1,6,4],[0,-1,"forms",".Form",1,7,4]]"#
    );
    assert_eq!(
        facts(source, "callees"),
        r#"[[0,-1,"os.path.join",1,5,4],[0,-1,"os.getcwd",1,6,4],[0,-1,"forms.Form",1,7,4]]"#
    );
    assert_eq!(facts(source, "names"), r#"[[0,-1,"Widget",2,8,11]]"#);
}

#[test]
fn listed_names_are_the_imports_used_only_inside_literals() {
    let source = r#"from app.temporal import WORKFLOWS, create_schedule, run_job, load_all
from app import plugins

schedules = [create_schedule, run_job]
specs = [("queue", OTHER + WORKFLOWS, [*load_all])]
run_job()
plugins.register()
"#;
    assert_eq!(
        facts(source, "listed"),
        r#"[[-1,-1,"create_schedule",1,4,13],[-1,-1,"WORKFLOWS",1,5,27],[-1,-1,"load_all",1,5,40]]"#
    );
}

#[test]
fn watched_keys_and_branches() {
    let source = r#"QUERY = {"kind": "TrendsQuery", "series": []}
if mode == "fast":
    run({"kind": Kind.FAST})
"#;
    assert_eq!(
        facts(source, "watched"),
        r#"[[-1,-1,1,17,0,"kind",{"s":"TrendsQuery"},"","assign:QUERY"],[-1,-1,3,17,32,"kind","Kind.FAST","","arg:0:run expr"]]"#
    );
    assert_eq!(
        facts(source, "branches"),
        r#"[[-1,-1,2,0,0,3,{"cmp":"mode","cs":[{"s":"fast"}],"ops":["=="]}]]"#
    );
    let unwatched = ExtractConfig::new(strings(&["app"]));
    assert!(line_with(source, &unwatched).get("watched").is_none());
}

#[test]
fn records_carry_only_the_requested_kinds_and_name_them_in_cfg() {
    let source = r#"import importlib
from app.models import Thing

def load():
    try:
        importlib.import_module("app.plugins", package=None)
    except ImportError:
        pass
    x = Thing.objects
    return x.first()
"#;
    let full = line_with(source, &config());
    assert_eq!(full["cfg"], "0456280f");
    assert_eq!(
        kinds_of(&full),
        strings(&[
            "callees", "calls", "defs", "derived", "imports", "refs", "strs", "uses"
        ])
    );
    let cases: [(&[Kind], &str, &[&str]); 2] = [
        (
            &[Kind::Imports, Kind::Strs],
            "e7f751b1",
            &["imports", "strs"],
        ),
        (&[Kind::Derived], "e61ee8e4", &["derived"]),
    ];
    for (kinds, cfg, expected) in cases {
        let selected = ExtractConfig::new(strings(&["app"])).with_kinds(Kinds::of(kinds));
        let line = line_with(source, &selected);
        assert_eq!(line["cfg"], cfg);
        assert_eq!(kinds_of(&line), strings(expected));
        for kind in expected {
            assert_eq!(line[*kind], full[*kind]);
        }
    }
}

#[test]
fn lite_kinds_get_their_own_cfg() {
    let lite = config().with_kinds(Kinds::of(&[Kind::Imports, Kind::Defs, Kind::Assigns]));
    assert_eq!(lite.config_id(), "fc74c9c7");
    assert_ne!(lite.cache_namespace(), config().cache_namespace());
}

#[test]
fn files_cpython_rejects_get_a_status_and_no_facts() {
    let cases: [(&[u8], Status); 3] = [
        (b"def broken(:\n    pass\n", Status::SyntaxError),
        (b"NAME = \"\xff\"\n", Status::DecodeError),
        (b"x = 1\0\n", Status::SyntaxError),
    ];
    for (raw, status) in cases {
        let record = record(raw);
        assert_eq!(record.status, status);
        assert!(record.facts.defs.is_empty() && record.facts.assigns.is_empty());
    }
}
