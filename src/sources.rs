//! The source sets `engine-sources.txt` defines, and their ids, as that file's header specifies.
//! build.rs includes this file to make FACTS_ID and NATIVE_ID; hoglint/ledger/envelope.py
//! computes the same ids in Python. Pure: callers list the files and read their bytes.

use sha2::{Digest, Sha256};

pub const SECTIONS: [&str; 3] = ["facts", "native", "engine"];
pub const FACTS: usize = 0;
pub const NATIVE: usize = 1;
pub const ENGINE: usize = 2;
const PROTOCOL_LINE: &str = "protocol ";
const DIGEST_BYTES: usize = 8;

type Pattern = Vec<Vec<char>>;

/// One section: the files it adds match one of its patterns and none of its `!` patterns.
#[derive(Default)]
pub struct Section {
    patterns: Vec<Pattern>,
    excludes: Vec<Pattern>,
}

pub struct SourceList {
    pub protocol: String,
    pub sections: [Section; 3],
}

fn pattern(text: &str) -> Pattern {
    text.split('/').map(|part| part.chars().collect()).collect()
}

/// fnmatch for one path segment: `*`, `?`, `[set]` and `[!set]`.
fn segment_match(pattern: &[char], text: &[char]) -> bool {
    match pattern.first() {
        None => text.is_empty(),
        Some('*') => (0..=text.len()).any(|skip| segment_match(&pattern[1..], &text[skip..])),
        Some('?') => !text.is_empty() && segment_match(&pattern[1..], &text[1..]),
        Some('[') => {
            let Some(end) = pattern
                .iter()
                .skip(2)
                .position(|&c| c == ']')
                .map(|at| at + 2)
            else {
                return text.first() == Some(&'[') && segment_match(&pattern[1..], &text[1..]);
            };
            let Some(&c) = text.first() else {
                return false;
            };
            let (negate, set) = match pattern[1] {
                '!' => (true, &pattern[2..end]),
                _ => (false, &pattern[1..end]),
            };
            let mut found = false;
            let mut i = 0;
            while i < set.len() {
                if i + 2 < set.len() && set[i + 1] == '-' {
                    found |= set[i] <= c && c <= set[i + 2];
                    i += 3;
                } else {
                    found |= set[i] == c;
                    i += 1;
                }
            }
            found != negate && segment_match(&pattern[end + 1..], &text[1..])
        }
        Some(&p) => text.first() == Some(&p) && segment_match(&pattern[1..], &text[1..]),
    }
}

/// `**` matches any number of segments, none included; other segments match with fnmatch.
fn glob_match(pattern: &[Vec<char>], path: &[Vec<char>]) -> bool {
    let Some(first) = pattern.first() else {
        return path.is_empty();
    };
    if first.as_slice() == ['*', '*'] {
        return (0..=path.len()).any(|skip| glob_match(&pattern[1..], &path[skip..]));
    }
    !path.is_empty() && segment_match(first, &path[0]) && glob_match(&pattern[1..], &path[1..])
}

impl Section {
    fn adds(&self, path: &[Vec<char>]) -> bool {
        self.patterns.iter().any(|p| glob_match(p, path))
            && !self.excludes.iter().any(|p| glob_match(p, path))
    }
}

impl SourceList {
    pub fn parse(text: &str) -> Result<SourceList, String> {
        let mut list = SourceList {
            protocol: String::new(),
            sections: Default::default(),
        };
        let mut current: Option<usize> = None;
        for raw in text.lines() {
            let line = raw.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if let Some(name) = line
                .strip_prefix('[')
                .and_then(|rest| rest.strip_suffix(']'))
            {
                let at = SECTIONS.iter().position(|section| *section == name);
                current = Some(at.ok_or_else(|| format!("unknown section [{name}]"))?);
                continue;
            }
            let Some(section) = current else {
                return Err(format!("{line:?} is outside a section"));
            };
            if let Some(version) = line.strip_prefix(PROTOCOL_LINE) {
                list.protocol = version.trim().to_string();
            } else if let Some(excluded) = line.strip_prefix('!') {
                list.sections[section].excludes.push(pattern(excluded));
            } else {
                list.sections[section].patterns.push(pattern(line));
            }
        }
        Ok(list)
    }

    /// The files of `section` among `paths` (crate-relative, `/`-separated): those any section
    /// up to it adds, sorted by path bytes.
    pub fn files(&self, section: usize, paths: &[String]) -> Vec<String> {
        let mut out: Vec<String> = paths
            .iter()
            .filter(|rel| {
                let parts: Vec<Vec<char>> =
                    rel.split('/').map(|part| part.chars().collect()).collect();
                self.sections[..=section].iter().any(|s| s.adds(&parts))
            })
            .cloned()
            .collect();
        out.sort();
        out
    }

    /// The id of `section` over its files, given as (path, bytes) in `files` order.
    pub fn digest(&self, section: usize, files: &[(String, Vec<u8>)]) -> String {
        let mut hasher = Sha256::new();
        if section == ENGINE {
            hasher.update(format!("{PROTOCOL_LINE}{}", self.protocol).as_bytes());
            hasher.update([0]);
        }
        for (rel, bytes) in files {
            hasher.update(rel.as_bytes());
            hasher.update([0]);
            hasher.update(bytes);
            hasher.update([0]);
        }
        hasher.finalize()[..DIGEST_BYTES]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LIST: &str = "[facts]\nsrc/**/*.rs\n!src/link/**\nCargo.toml\nCargo.lock\n\n[native]\nsrc/link/**\nbinding/**\n\n[engine]\nprotocol 1\nhoglint/**/*.py\n";

    /// The files and input spellings of hoglint's tests/rules/test_attribution.py.
    const FILES: [(&str, &str); 11] = [
        ("engine-sources.txt", LIST),
        ("Cargo.toml", "[package]"),
        ("Cargo.lock", "v1"),
        ("src/lib.rs", "lib"),
        ("src/link/mod.rs", "link"),
        ("binding/Cargo.toml", "[binding]"),
        ("binding/src/lib.rs", "bind"),
        ("hoglint/cli.py", "cli"),
        ("tests/t.py", "not the engine"),
        ("src/notes.md", "not the engine"),
        ("target/debug/gen.rs", "build output"),
    ];

    fn spelled(input: &[u8]) -> String {
        Sha256::digest(input)[..DIGEST_BYTES]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    }

    fn id(section: usize) -> String {
        let list = SourceList::parse(LIST).unwrap();
        let paths: Vec<String> = FILES
            .iter()
            .map(|(p, _)| p.to_string())
            .filter(|p| !p.starts_with("target/"))
            .collect();
        let files: Vec<(String, Vec<u8>)> = list
            .files(section, &paths)
            .into_iter()
            .map(|rel| {
                let text = FILES.iter().find(|(p, _)| *p == rel).unwrap().1;
                (rel, text.as_bytes().to_vec())
            })
            .collect();
        list.digest(section, &files)
    }

    #[test]
    fn ids_match_the_python_test_vectors() {
        let facts = b"Cargo.lock\0v1\0Cargo.toml\0[package]\0src/lib.rs\0lib\0";
        let native: &[u8] = b"Cargo.lock\0v1\0Cargo.toml\0[package]\0binding/Cargo.toml\0[binding]\0binding/src/lib.rs\0bind\0src/lib.rs\0lib\0src/link/mod.rs\0link\0";
        let engine: &[u8] = b"protocol 1\0Cargo.lock\0v1\0Cargo.toml\0[package]\0binding/Cargo.toml\0[binding]\0binding/src/lib.rs\0bind\0hoglint/cli.py\0cli\0src/lib.rs\0lib\0src/link/mod.rs\0link\0";
        assert_eq!(id(FACTS), spelled(facts));
        assert_eq!(id(NATIVE), spelled(native));
        assert_eq!(id(ENGINE), spelled(engine));
    }
}
