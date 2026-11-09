//! One small Python snippet per fact kind. Expected rows come from the reference extractor.

use crate::{ExtractConfig, FileRecord, Status, extract, record_line, sha256_hex};

fn config() -> ExtractConfig {
    ExtractConfig::new(vec!["app".to_string()], vec!["import_module".to_string()])
}

fn record(raw: &[u8]) -> FileRecord {
    extract(raw, sha256_hex(raw), &config())
}

/// The JSON of one fact list of the record for `source`, or "" when the list is empty.
fn facts(source: &str, kind: &str) -> String {
    let line = record_line(&record(source.as_bytes()), "cfg", "f.py").unwrap();
    let value: serde_json::Value = serde_json::from_str(&line).unwrap();
    value.get(kind).map(|v| v.to_string()).unwrap_or_default()
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
fn refs_follow_tracked_names_and_derived_locals() {
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
        r#"[[0,-1,5,8,"Thing",".objects","assign:x"],[0,-1,6,8,"x",".filter()","assign:y",0],[0,-1,7,11,"y",".first()","elt:tuple return",0],[0,-1,7,22,"models",".Other","elt:tuple return"],[-1,-1,12,4,"Thing",".check()","expr"]]"#
    );
    assert_eq!(
        facts(source, "site_flags"),
        "[[5,8,4],[6,8,4],[7,11,4],[7,22,4],[12,4,1]]"
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
        facts(source, "refs"),
        r#"[[1,-1,5,15,"later",".go()","return",0],[0,-1,6,12,"first",".all()","assign:later",0],[0,-1,7,12,"Thing",".objects","assign:first"]]"#
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
        r#"[[0,-1,5,8,"importlib.import_module",[{"s":"app.plugins"}],[["package",null]]],[0,-1,8,4,"apps.get_model",[{"s":"app"},{"s":"Thing"}],[]],[0,-1,10,4,"register_hook",[{"tuple":["a.b","c"]},{"o":"List"}],[]]]"#
    );
    assert_eq!(
        facts(source, "site_flags"),
        "[[5,8,20],[5,32,20],[8,4,4],[10,4,4]]"
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
        r#"[[-1,-1,1,7,"app.models.Thing","","assign:PATH"],[-1,-1,2,9,"app.views.Home","","assign:PARTS",[null,"Home"]],[-1,-1,6,13,"app.typing.Alias","","assign:TARGET"]]"#
    );
    assert_eq!(
        facts(source, "tags"),
        r#"[[-1,-1,4,7,"Box"],[-1,-1,4,7,"Widget"]]"#
    );
    assert_eq!(facts(source, "names"), r#"[[-1,-1,"Thing",1,3,8]]"#);
    assert_eq!(facts(source, "site_flags"), "[[6,13,1]]");
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
fn watched_keys_and_branches() {
    let source = r#"QUERY = {"kind": "TrendsQuery", "series": []}
if mode == "fast":
    run({"kind": Kind.FAST})
"#;
    assert_eq!(
        facts(source, "watched"),
        r#"[[-1,-1,1,17,"kind",{"s":"TrendsQuery"},"","assign:QUERY"],[-1,-1,3,17,"kind","Kind.FAST","","arg:0:run expr"]]"#
    );
    assert_eq!(
        facts(source, "branches"),
        r#"[[-1,-1,2,0,3,{"cmp":"mode","cs":[{"s":"fast"}],"ops":["=="]}]]"#
    );
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
