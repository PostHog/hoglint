//! Path globs, module names for paths, and the module index (hoglint/core/config.py PathGlobs,
//! hoglint/core/modules.py).

use super::facts::FactSet;
use super::intern::{Interner, Sid};
use regex::Regex;
use rustc_hash::{FxHashMap, FxHashSet};

/// Gitignore-style globs. A pattern without a slash matches one path component at any depth; a
/// pattern with a slash is anchored at the repo root and also matches the path's parents.
pub struct PathGlobs {
    component: Vec<Regex>,
    anchored: Vec<Regex>,
}

fn translate_segment(segment: &str) -> String {
    let chars: Vec<char> = segment.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        match c {
            '*' => out.push_str("[^/]*"),
            '?' => out.push_str("[^/]"),
            '[' => match chars[i + 1..].iter().position(|&x| x == ']') {
                None => out.push_str(&regex::escape("[")),
                Some(offset) => {
                    let end = i + 1 + offset;
                    let mut body: String = chars[i + 1..end].iter().collect();
                    body = body.replace('\\', "\\\\");
                    if let Some(rest) = body.strip_prefix('!') {
                        body = format!("^{rest}");
                    }
                    out.push('[');
                    out.push_str(&body);
                    out.push(']');
                    i = end;
                }
            },
            other => out.push_str(&regex::escape(&other.to_string())),
        }
        i += 1;
    }
    out
}

fn translate(pattern: &str) -> String {
    let segments: Vec<&str> = pattern.split('/').collect();
    let mut out = String::from("^");
    for (i, segment) in segments.iter().enumerate() {
        let last = i == segments.len() - 1;
        if *segment == "**" {
            out.push_str(if last { ".*" } else { "(?:[^/]+/)*" });
        } else {
            out.push_str(&translate_segment(segment));
            if !last {
                out.push('/');
            }
        }
    }
    out.push('$');
    out
}

impl PathGlobs {
    pub fn new(patterns: &[String]) -> Result<PathGlobs, regex::Error> {
        let mut globs = PathGlobs {
            component: Vec::new(),
            anchored: Vec::new(),
        };
        for pattern in patterns {
            let stripped = pattern.trim_matches('/');
            if stripped.is_empty() {
                continue;
            }
            let regex = Regex::new(&translate(stripped))?;
            if stripped.contains('/') {
                globs.anchored.push(regex);
            } else {
                globs.component.push(regex);
            }
        }
        Ok(globs)
    }

    pub fn matches(&self, path: &str) -> bool {
        let parts: Vec<&str> = path.split('/').collect();
        if !self.component.is_empty()
            && parts
                .iter()
                .any(|part| self.component.iter().any(|re| re.is_match(part)))
        {
            return true;
        }
        if self.anchored.is_empty() {
            return false;
        }
        let mut end = 0;
        for (i, part) in parts.iter().enumerate() {
            end += part.len() + usize::from(i > 0);
            let prefix = &path[..end];
            if self.anchored.iter().any(|re| re.is_match(prefix)) {
                return true;
            }
        }
        false
    }
}

/// A source root as a path prefix without slashes; the repo root is "".
pub fn normalize_root(root: &str) -> String {
    let stripped = root.trim_matches('/');
    if stripped.is_empty() || stripped == "." {
        String::new()
    } else {
        stripped.to_string()
    }
}

/// Dotted module name of a path; the longest matching source root wins. `external` maps a
/// third-party file to its path inside its site-packages root.
pub struct ModuleNamer {
    roots: Vec<String>,
    pub external: FxHashMap<String, String>,
}

impl ModuleNamer {
    pub fn new(source_roots: &[String], external: FxHashMap<String, String>) -> ModuleNamer {
        let mut roots: Vec<String> = source_roots.iter().map(|r| normalize_root(r)).collect();
        roots.sort_by_key(|root| std::cmp::Reverse(root.len()));
        ModuleNamer { roots, external }
    }

    fn relative<'a>(&'a self, path: &'a str) -> &'a str {
        if let Some(inside) = self.external.get(path) {
            return inside;
        }
        for root in &self.roots {
            if root.is_empty() {
                return path;
            }
            if path.len() > root.len()
                && path.starts_with(root.as_str())
                && path.as_bytes()[root.len()] == b'/'
            {
                return &path[root.len() + 1..];
            }
        }
        path
    }

    /// (dotted module, is_package).
    pub fn module_of(&self, path: &str) -> (String, bool) {
        let rel = self.relative(path);
        let stem = match rel.strip_suffix(".pyi") {
            Some(stem) => stem,
            None => rel.strip_suffix(".py").unwrap_or(rel),
        };
        let mut parts: Vec<&str> = stem.split('/').collect();
        let is_package = parts.last() == Some(&"__init__");
        if is_package {
            parts.pop();
        }
        (parts.join("."), is_package)
    }
}

/// Module name <-> file over repo and site-packages files. Repo files win over site-packages,
/// `.py` over `.pyi`; a directory with modules but no `__init__` is a namespace package.
pub struct ModuleIndex {
    pub path_of: FxHashMap<Sid, u32>,
    /// Modules with a file, in the order the Python index inserts them.
    pub modules: Vec<Sid>,
    pub module_of_file: Vec<Option<(Sid, bool)>>,
    pub package: FxHashMap<Sid, bool>,
    pub dirs: FxHashSet<Sid>,
    pub stub_of: FxHashMap<Sid, u32>,
    pub repo_heads: FxHashSet<Sid>,
    pub site: Vec<bool>,
}

impl ModuleIndex {
    pub fn build(
        it: &mut Interner,
        facts: &FactSet,
        namer: &ModuleNamer,
        first_party: &[String],
    ) -> ModuleIndex {
        let site: Vec<bool> = facts
            .files
            .iter()
            .map(|f| namer.external.contains_key(&*f.path))
            .collect();
        let mut index = ModuleIndex {
            path_of: FxHashMap::default(),
            modules: Vec::new(),
            module_of_file: vec![None; facts.files.len()],
            package: FxHashMap::default(),
            dirs: FxHashSet::default(),
            stub_of: FxHashMap::default(),
            repo_heads: first_party.iter().map(|head| it.intern(head)).collect(),
            site,
        };
        let mut order: Vec<u32> = (0..facts.files.len() as u32).collect();
        let key = |i: &u32| {
            let path = &*facts.files[*i as usize].path;
            (index.site[*i as usize], path.ends_with(".pyi"), path)
        };
        order.sort_by(|a, b| key(a).cmp(&key(b)));
        for file in order {
            let path = &*facts.files[file as usize].path;
            let (name, is_package) = namer.module_of(path);
            if name.is_empty() {
                continue;
            }
            let module = it.intern(&name);
            index.module_of_file[file as usize] = Some((module, is_package));
            let parts = it.segs(module);
            if !index.site[file as usize] {
                index.repo_heads.insert(parts[0]);
            }
            for end in 1..parts.len() {
                let dir = it.from_segs(&parts[..end]);
                index.dirs.insert(dir);
            }
            if let Some(&current) = index.path_of.get(&module) {
                let current_path = &*facts.files[current as usize].path;
                if path.ends_with(".pyi") && !current_path.ends_with(".pyi") {
                    index.stub_of.entry(module).or_insert(file);
                }
                continue;
            }
            index.path_of.insert(module, file);
            index.modules.push(module);
            index.package.insert(module, is_package);
        }
        index
    }

    pub fn exists(&self, module: Sid) -> bool {
        self.path_of.contains_key(&module) || self.dirs.contains(&module)
    }

    pub fn is_file_module(&self, module: Sid) -> bool {
        self.path_of.contains_key(&module)
    }

    pub fn is_package(&self, module: Sid) -> bool {
        match self.package.get(&module) {
            Some(&flag) => flag,
            None => self.dirs.contains(&module),
        }
    }

    /// Third-party: loaded from site-packages, or named under a head no repo file has.
    pub fn is_external(&self, it: &mut Interner, module: Sid) -> bool {
        if let Some(&file) = self.path_of.get(&module) {
            return self.site[file as usize];
        }
        let head = it.head(module);
        !self.repo_heads.contains(&head)
    }

    /// The longest dotted prefix (itself included) that is a module or namespace package.
    pub fn longest_module(&self, it: &mut Interner, dotted: Sid) -> Option<(Sid, usize)> {
        let parts = it.segs(dotted);
        for end in (1..=parts.len()).rev() {
            let candidate = it.from_segs(&parts[..end]);
            if self.exists(candidate) {
                return Some((candidate, end));
            }
        }
        None
    }
}
