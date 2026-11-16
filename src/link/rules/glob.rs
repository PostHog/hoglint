//! Label globs (hoglint/globs.py `compile_label_glob`): `**/` spans directories, `**` spans
//! anything, `*` and `?` stay inside one segment, and `{name}` captures one segment. A name
//! that repeats must match the same text again.
//!
//! The matcher backtracks in the order Python's `re` tries the same regex, greedy first, so a
//! path gets the same captures here as in the Python labeler, which still matches label globs.

/// One piece of a glob.
#[derive(Clone, PartialEq, Eq, Debug)]
enum Token {
    /// One character that matches only itself.
    Char(char),
    /// `*`: `[^/]*`.
    Star,
    /// `?`: `[^/]`.
    Quest,
    /// `**/`: `(?:.*/)?`.
    Dirs,
    /// `**`: `.*`.
    Any,
    /// `{name}`: `[^/]+`, captured; the index into the glob's names.
    Capture(usize),
    /// A repeated `{name}`: the text the first capture took.
    Again(usize),
}

#[derive(Clone, Debug)]
pub struct Glob {
    tokens: Vec<Token>,
    names: Vec<String>,
    /// The text every match starts and ends with, so most paths fail before the matcher runs.
    prefix: String,
    suffix: String,
}

/// Captures of a match: (name, text) in the order the names first appear.
pub type Captures = Vec<(String, String)>;

/// `.` in Python's `re`: any character but a newline.
fn any_char(c: char) -> bool {
    c != '\n'
}

fn in_segment(c: char) -> bool {
    c != '/'
}

impl Glob {
    pub fn new(pattern: &str) -> Result<Glob, String> {
        let chars: Vec<char> = pattern.chars().collect();
        let mut tokens = Vec::new();
        let mut names: Vec<String> = Vec::new();
        let mut literal = Vec::new();
        let mut at = 0;
        while at < chars.len() {
            let rest = &chars[at..];
            if rest.starts_with(&['*', '*', '/']) {
                tokens.push(Token::Dirs);
                at += 3;
                continue;
            }
            if rest.starts_with(&['*', '*']) {
                tokens.push(Token::Any);
                at += 2;
                continue;
            }
            match chars[at] {
                '{' => {
                    let Some(end) = rest.iter().position(|&c| c == '}') else {
                        return Err(format!("glob {pattern:?} opens a capture it never closes"));
                    };
                    let name: String = rest[1..end].iter().collect();
                    match names.iter().position(|known| *known == name) {
                        Some(index) => tokens.push(Token::Again(index)),
                        None => {
                            tokens.push(Token::Capture(names.len()));
                            names.push(name);
                        }
                    }
                    at += end + 1;
                    continue;
                }
                '*' => tokens.push(Token::Star),
                '?' => tokens.push(Token::Quest),
                c => tokens.push(Token::Char(c)),
            }
            at += 1;
        }
        for token in &tokens {
            literal.push(matches!(token, Token::Char(_)));
        }
        let first = literal.iter().position(|is| !is);
        let last = literal.iter().rposition(|is| !is);
        let text = |range: &[Token]| -> String {
            range
                .iter()
                .map(|token| match token {
                    Token::Char(c) => *c,
                    _ => unreachable!("only literal tokens"),
                })
                .collect()
        };
        let (prefix, suffix) = match (first, last) {
            (Some(first), Some(last)) => (text(&tokens[..first]), text(&tokens[last + 1..])),
            _ => (pattern.to_string(), pattern.to_string()),
        };
        Ok(Glob {
            tokens,
            names,
            prefix,
            suffix,
        })
    }

    /// The captures when the whole path matches, else None. An ASCII path is matched byte by
    /// byte, any other character by character.
    pub fn captures(&self, path: &str) -> Option<Captures> {
        if !path.starts_with(&self.prefix) || !path.ends_with(&self.suffix) {
            return None;
        }
        if path.is_ascii() {
            return self.captures_in(path.as_bytes());
        }
        let chars: Vec<char> = path.chars().collect();
        self.captures_in(&chars)
    }

    fn captures_in<T: Copy + PartialEq + Into<char>>(&self, text: &[T]) -> Option<Captures> {
        let mut found: Vec<(usize, usize)> = vec![(0, 0); self.names.len()];
        if !self.walk(text, 0, 0, &mut found) {
            return None;
        }
        Some(
            self.names
                .iter()
                .zip(found)
                .map(|(name, (start, end))| {
                    (
                        name.clone(),
                        text[start..end].iter().map(|&c| c.into()).collect(),
                    )
                })
                .collect(),
        )
    }

    pub fn is_match(&self, path: &str) -> bool {
        self.captures(path).is_some()
    }

    /// Whether `tokens[token..]` matches `chars[at..]` to its end; `found` holds the span of
    /// each capture taken so far.
    fn walk<T: Copy + PartialEq + Into<char>>(
        &self,
        chars: &[T],
        token: usize,
        at: usize,
        found: &mut [(usize, usize)],
    ) -> bool {
        let Some(current) = self.tokens.get(token) else {
            return at == chars.len();
        };
        let next = token + 1;
        match current {
            Token::Char(c) => {
                chars.get(at).is_some_and(|&x| x.into() == *c)
                    && self.walk(chars, next, at + 1, found)
            }
            Token::Quest => {
                chars.get(at).is_some_and(|&c| in_segment(c.into()))
                    && self.walk(chars, next, at + 1, found)
            }
            Token::Star => {
                let longest = run_length(chars, at, in_segment);
                (0..=longest)
                    .rev()
                    .any(|taken| self.walk(chars, next, at + taken, found))
            }
            Token::Any => {
                let longest = run_length(chars, at, any_char);
                (0..=longest)
                    .rev()
                    .any(|taken| self.walk(chars, next, at + taken, found))
            }
            Token::Dirs => {
                let longest = run_length(chars, at, any_char);
                let slashes = (0..longest)
                    .rev()
                    .filter(|&taken| chars[at + taken].into() == '/');
                for taken in slashes {
                    if self.walk(chars, next, at + taken + 1, found) {
                        return true;
                    }
                }
                self.walk(chars, next, at, found)
            }
            Token::Capture(index) => {
                let longest = run_length(chars, at, in_segment);
                for taken in (1..=longest).rev() {
                    let saved = found[*index];
                    found[*index] = (at, at + taken);
                    if self.walk(chars, next, at + taken, found) {
                        return true;
                    }
                    found[*index] = saved;
                }
                false
            }
            Token::Again(index) => {
                let (start, end) = found[*index];
                let length = end - start;
                at + length <= chars.len()
                    && chars[start..end] == chars[at..at + length]
                    && self.walk(chars, next, at + length, found)
            }
        }
    }
}

/// How many characters from `at` on pass `test`.
fn run_length<T: Copy + Into<char>>(chars: &[T], at: usize, test: fn(char) -> bool) -> usize {
    chars[at..].iter().take_while(|&&c| test(c.into())).count()
}

/// The text of a capture by name.
pub fn capture<'c>(captures: &'c [(String, String)], name: &str) -> Option<&'c str> {
    captures
        .iter()
        .find(|(key, _)| key == name)
        .map(|(_, value)| value.as_str())
}

/// Whether `name` is a capture name `fill` replaces: `[A-Za-z_][A-Za-z0-9_]*`.
fn capture_name(name: &str) -> bool {
    let mut chars = name.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    (first == '_' || first.is_ascii_alphabetic())
        && chars.all(|c| c == '_' || c.is_ascii_alphanumeric())
}

/// A `{name}` template with captures filled in (hoglint/globs.py `fill`); a name with no
/// capture stays as written.
pub fn fill(template: &str, captures: &[(String, String)]) -> String {
    let mut out = String::new();
    let mut rest = template;
    while let Some(open) = rest.find('{') {
        out.push_str(&rest[..open]);
        let after = &rest[open + 1..];
        let Some(close) = after.find('}') else {
            out.push_str(&rest[open..]);
            return out;
        };
        let name = &after[..close];
        if !capture_name(name) {
            out.push('{');
            rest = after;
            continue;
        }
        match capture(captures, name) {
            Some(value) => out.push_str(value),
            None => {
                out.push('{');
                out.push_str(name);
                out.push('}');
            }
        }
        rest = &after[close + 1..];
    }
    out.push_str(rest);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn caps(pattern: &str, path: &str) -> Option<Vec<(String, String)>> {
        Glob::new(pattern).expect("a glob").captures(path)
    }

    #[test]
    fn globs_match_and_capture_as_python_re_does() {
        let pair = |name: &str, value: &str| (name.to_string(), value.to_string());
        assert_eq!(
            caps("products/{product}/**", "products/billing/a/b.py"),
            Some(vec![pair("product", "billing")])
        );
        assert_eq!(caps("**/tests/**", "tests/x.py"), Some(vec![]));
        assert_eq!(caps("**/test_*.py", "a/b/test_x.py"), Some(vec![]));
        assert_eq!(caps("**/test_*.py", "a/b/x_test.py"), None);
        assert_eq!(
            caps("{a}/**/{a}.py", "x/y/x.py"),
            Some(vec![pair("a", "x")])
        );
        assert_eq!(caps("{a}/**/{a}.py", "x/y/z.py"), None);
        assert_eq!(
            caps("{a}{b}.py", "abc.py"),
            Some(vec![pair("a", "ab"), pair("b", "c")])
        );
        assert_eq!(caps("a/*.py", "a/b/c.py"), None);
    }

    #[test]
    fn fill_keeps_names_without_a_capture() {
        let captures = vec![("product".to_string(), "billing".to_string())];
        assert_eq!(fill("{product}-{other}", &captures), "billing-{other}");
        assert_eq!(fill("{9x}{product}", &captures), "{9x}billing");
        assert_eq!(fill("open {product", &captures), "open {product");
    }
}

/// The files an export covers: its `paths`, read relative to the directory `within` names
/// (hoglint/globs.py `ExportPaths`; `within` may capture `{name}` parts, empty is the repo root).
#[derive(Clone, Debug)]
pub struct ExportPaths {
    within: Option<(String, Glob)>,
    paths: Vec<Glob>,
}

impl ExportPaths {
    pub fn new(within: &str, paths: &[String]) -> Result<ExportPaths, String> {
        let within = match within {
            "" => None,
            root => Some((root.to_string(), Glob::new(&format!("{root}/**"))?)),
        };
        let paths = paths
            .iter()
            .map(|path| Glob::new(path))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(ExportPaths { within, paths })
    }

    /// The directory `within` names for a path the export covers, its captures filled in
    /// (`products/billing`); None for a path it does not cover.
    pub fn covered_root(&self, path: &str) -> Option<String> {
        let (root, relative) = match &self.within {
            None => (String::new(), path),
            Some((within, glob)) => {
                let root = fill(within, &glob.captures(path)?);
                let relative = path.get(root.len() + 1..).unwrap_or("");
                (root, relative)
            }
        };
        self.paths
            .iter()
            .any(|glob| glob.is_match(relative))
            .then_some(root)
    }

    /// Whether the path lies inside `within` and one of `paths`.
    pub fn covers(&self, path: &str) -> bool {
        self.covered_root(path).is_some()
    }
}
