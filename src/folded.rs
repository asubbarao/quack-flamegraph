//! Folded-stack parser (Brendan Gregg / sample / xctrace collapse).
//!
//! Count is the last space-separated field. Zero-sample parent frames are
//! dropped. An optional Instruments `Thread_…:` prefix is stripped.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FoldedStack {
    pub frames: Vec<String>,
    pub samples: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseError {
    pub message: String,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for ParseError {}

/// Parse one folded line.
///
/// `Ok(None)` means skip (blank or zero samples). `Err` means the scan must fail.
pub fn parse_folded_line(line: &str) -> Result<Option<FoldedStack>, ParseError> {
    let line = line.trim();
    if line.is_empty() {
        return Ok(None);
    }
    let Some((stack_part, count_part)) = line.rsplit_once(' ') else {
        return Err(ParseError {
            message: "missing sample count".into(),
        });
    };
    let samples: i64 = count_part.parse().map_err(|_| ParseError {
        message: "missing sample count".into(),
    })?;
    if samples < 0 {
        return Err(ParseError {
            message: "negative sample count".into(),
        });
    }
    if samples == 0 {
        return Ok(None);
    }

    let stack_part = strip_thread_prefix(stack_part);
    let frames: Vec<String> = stack_part
        .split(';')
        .filter(|frame| !frame.is_empty())
        .map(str::to_string)
        .collect();
    if frames.is_empty() {
        return Err(ParseError {
            message: "missing frames".into(),
        });
    }
    Ok(Some(FoldedStack { frames, samples }))
}

fn strip_thread_prefix(stack: &str) -> &str {
    let Some(rest) = stack.strip_prefix("Thread_") else {
        return stack;
    };
    match rest.split_once(':') {
        Some((_, after)) => after.strip_prefix(';').unwrap_or(after),
        None => stack,
    }
}

/// Read a folded file from disk. Missing files report `IO Error`; malformed
/// lines report `Invalid Input Error`.
pub fn load_folded_file(path: impl AsRef<std::path::Path>) -> Result<Vec<FoldedStack>, ParseError> {
    let path = path.as_ref();
    let text = std::fs::read_to_string(path).map_err(|err| ParseError {
        message: format!("IO Error: cannot read {}: {err}", path.display()),
    })?;
    parse_folded(&text).map_err(|err| ParseError {
        message: format!("Invalid Input Error: {}", err.message),
    })
}

/// Parse a whole folded document, skipping blank and zero-sample lines.
pub fn parse_folded(text: &str) -> Result<Vec<FoldedStack>, ParseError> {
    let mut out = Vec::new();
    for (i, line) in text.lines().enumerate() {
        match parse_folded_line(line) {
            Ok(Some(stack)) => out.push(stack),
            Ok(None) => {}
            Err(err) => {
                return Err(ParseError {
                    message: format!("line {}: {}", i + 1, err.message),
                });
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_folded_line_returns_frames_and_samples() {
        let got = parse_folded_line("main;foo;bar 10").unwrap().unwrap();
        assert_eq!(got.frames, vec!["main", "foo", "bar"]);
        assert_eq!(got.samples, 10);
    }

    #[test]
    fn parse_folded_line_drops_zero_samples() {
        assert_eq!(parse_folded_line("main;foo 0").unwrap(), None);
    }

    #[test]
    fn parse_folded_line_skips_blank() {
        assert_eq!(parse_folded_line("").unwrap(), None);
        assert_eq!(parse_folded_line("   ").unwrap(), None);
    }

    #[test]
    fn parse_folded_line_strips_thread_prefix() {
        let line = "Thread_5381148.DispatchQueue_1:;dyld`start;spur`main 12";
        let got = parse_folded_line(line).unwrap().unwrap();
        assert_eq!(got.frames, vec!["dyld`start", "spur`main"]);
        assert_eq!(got.samples, 12);
    }

    #[test]
    fn parse_folded_line_uses_last_field_as_count() {
        let got = parse_folded_line("foo bar;baz 7").unwrap().unwrap();
        assert_eq!(got.frames, vec!["foo bar", "baz"]);
        assert_eq!(got.samples, 7);
    }

    #[test]
    fn parse_folded_line_rejects_missing_count() {
        let err = parse_folded_line("main;foo").unwrap_err();
        assert!(err.message.contains("count"), "{}", err.message);
    }

    #[test]
    fn parse_folded_line_rejects_negative_count() {
        let err = parse_folded_line("main;foo -1").unwrap_err();
        assert!(err.message.contains("negative"), "{}", err.message);
    }

    #[test]
    fn parse_folded_drops_zeros_and_keeps_nonzero() {
        let text = "main;foo 10\nmain;foo 0\nmain;bar 20\n";
        let stacks = parse_folded(text).unwrap();
        assert_eq!(stacks.len(), 2);
        assert_eq!(stacks[0].samples, 10);
        assert_eq!(stacks[1].samples, 20);
        assert_eq!(
            stacks.iter().map(|s| s.samples).sum::<i64>(),
            30
        );
    }

    #[test]
    fn load_folded_file_reads_tiny_fixture() {
        let stacks = load_folded_file("test/data/tiny.folded").unwrap();
        assert_eq!(stacks.len(), 3);
        assert_eq!(stacks.iter().map(|s| s.samples).sum::<i64>(), 35);
    }

    #[test]
    fn load_folded_file_missing_is_io_error() {
        let err = load_folded_file("test/data/does-not-exist.folded").unwrap_err();
        assert!(err.message.contains("IO Error"), "{}", err.message);
    }

    #[test]
    fn load_folded_file_bad_line_is_invalid_input() {
        let err = load_folded_file("test/data/bad.folded").unwrap_err();
        assert!(
            err.message.contains("Invalid Input Error") || err.message.contains("count"),
            "{}",
            err.message
        );
    }
}
