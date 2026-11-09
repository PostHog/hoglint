//! Source text as CPython reads it, line positions, and the parser entry point.

use crate::facts::Status;
use ruff_python_ast::{Mod, PythonVersion};
use ruff_python_parser::{Mode, ParseOptions, Parsed};
use ruff_text_size::TextSize;
use std::borrow::Cow;

/// The oracle is CPython 3.14 `ast`: syntax that ruff gates on a newer version (lazy imports,
/// unpacking in comprehensions) is a syntax error here too.
pub fn parse_py314(text: &str, mode: Mode) -> Parsed<Mod> {
    ruff_python_parser::parse_unchecked(
        text,
        ParseOptions::from(mode).with_target_version(PythonVersion::PY314),
    )
}

/// UTF-8 text without a BOM and with `\n` line breaks, or the status of a file CPython rejects.
pub fn source_text(raw: &[u8]) -> Result<Cow<'_, str>, Status> {
    let Ok(text) = std::str::from_utf8(raw) else {
        return Err(Status::DecodeError);
    };
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    if text.contains('\0') {
        return Err(Status::SyntaxError);
    }
    // CPython translates \r\n and lone \r to \n before tokenizing; columns stay the same.
    if text.contains('\r') {
        return Ok(Cow::Owned(text.replace("\r\n", "\n").replace('\r', "\n")));
    }
    Ok(Cow::Borrowed(text))
}

pub struct LineIndex {
    starts: Vec<u32>,
}

impl LineIndex {
    /// The text is newline-normalized, so `\n` is the only line break.
    pub fn new(text: &str) -> LineIndex {
        let mut starts = vec![0];
        for (i, b) in text.bytes().enumerate() {
            if b == b'\n' {
                starts.push(i as u32 + 1);
            }
        }
        LineIndex { starts }
    }

    /// (1-based line, 0-based byte column).
    pub fn pos(&self, offset: TextSize) -> (i64, i64) {
        let off = offset.to_u32();
        let line = self.starts.partition_point(|&s| s <= off) - 1;
        (line as i64 + 1, (off - self.starts[line]) as i64)
    }

    pub fn line(&self, offset: TextSize) -> i64 {
        self.pos(offset).0
    }
}
