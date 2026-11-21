//! Message bodies: EFMData sidecars and HTML to text.
//!
//! New Outlook stores most full bodies inline in the class 00ca body object. Larger ones live
//! outside the store as gzip files at `Files/S0/<N>/EFMData/<id>.dat`; the body slot then holds
//! that file's profile-relative path (`~/Files/S0/<N>/EFMData/<id>.dat`). Only files found under
//! the given `Files` folder are ever opened, so a stored path cannot reach anywhere else.

use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

mod parse_budget;
mod split;
pub use split::split_html;

const MAX_SIDECAR: u64 = 32 << 20;

#[derive(Default)]
pub struct Sidecars {
    files: HashMap<String, PathBuf>,
    pub linked: usize,
    pub rejected: usize,
}

impl Sidecars {
    /// Index every `S0/*/EFMData/*.dat` under the profile's `Files` folder.
    /// Files are only read, never written; Outlook does not rewrite them.
    pub fn open(files_dir: &Path) -> Self {
        let mut files = HashMap::new();
        for root in fs::read_dir(files_dir.join("S0")).into_iter().flatten().flatten() {
            let n = root.file_name().to_string_lossy().into_owned();
            for entry in fs::read_dir(root.path().join("EFMData")).into_iter().flatten().flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                if name.ends_with(".dat") {
                    files.insert(format!("~/Files/S0/{n}/EFMData/{name}"), entry.path());
                }
            }
        }
        Sidecars { files, ..Default::default() }
    }

    pub fn len(&self) -> usize {
        self.files.len()
    }

    pub fn resolve(&self, stored_path: &str) -> Result<String, &'static str> {
        let path = self.files.get(stored_path).ok_or("sidecar_missing")?;
        let file = fs::File::open(path).map_err(|_| "sidecar_unreadable")?;
        let mut out = Vec::new();
        flate2::read::MultiGzDecoder::new(file).take(MAX_SIDECAR + 1).read_to_end(&mut out)
            .map_err(|_| "sidecar_decode_failed")?;
        if out.len() as u64 > MAX_SIDECAR {
            return Err("oversized");
        }
        String::from_utf8(out).map_err(|_| "sidecar_invalid_utf8")
    }
}

/// Tags whose open or close starts a new line. `<br>` always adds one, so it is handled apart.
const BLOCKS: &[&str] = &["p", "div", "tr", "li", "td", "h1", "h2", "h3", "blockquote", "hr"];

/// Strip tags and decode entities, yielding readable text that keeps the message's line breaks.
pub fn html_to_text(html: &str) -> String {
    let text = strip_tags(html)
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&apos;", "'")
        .replace("&mdash;", "-")
        .replace("&ndash;", "-");
    fold_lines(&text)
}

/// Tags become line breaks; whitespace in the HTML source, newlines included, is only a space.
fn strip_tags(html: &str) -> String {
    let mut out = String::with_capacity(html.len() / 2);
    let mut rest = html;
    let mut skip = 0usize;
    let mut line_start = true;
    while let Some(c) = rest.chars().next() {
        if c == '<' {
            let Some(e) = rest.find('>') else { break };
            let tag = &rest[1..e];
            let close = tag.starts_with('/');
            let name = tag.trim_start_matches('/')
                .split(|c: char| !c.is_ascii_alphanumeric()).next().unwrap_or_default()
                .to_ascii_lowercase();
            match name.as_str() {
                "script" | "style" | "head" if close => skip = skip.saturating_sub(1),
                "script" | "style" | "head" => skip += 1,
                "br" if skip == 0 => { out.push('\n'); line_start = true; }
                n if skip == 0 && !line_start && BLOCKS.contains(&n) => { out.push('\n'); line_start = true; }
                _ => {}
            }
            rest = &rest[e + 1..];
            continue;
        }
        if skip == 0 {
            if c.is_ascii_whitespace() {
                out.push(' ');
            } else if !c.is_control() {
                out.push(c);
                line_start = false;
            }
        }
        rest = &rest[c.len_utf8()..];
    }
    out
}

/// Every whitespace run, newlines included, becomes one space; the ends are trimmed.
pub fn fold_spaces(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Fold each line's whitespace and keep at most one blank line in a row.
fn fold_lines(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut blank = false;
    for line in text.split('\n') {
        let line = fold_spaces(line);
        if line.is_empty() {
            blank = !out.is_empty();
            continue;
        }
        if !out.is_empty() {
            out.push_str(if blank { "\n\n" } else { "\n" });
        }
        out.push_str(&line);
        blank = false;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn sidecar_limit_requires_eof_and_an_overflow_sentinel() {
        let dir = std::env::temp_dir().join(format!("hxprobe-limit-{}", std::process::id()));
        let efm = dir.join("S0/3/EFMData");
        fs::create_dir_all(&efm).unwrap();
        for size in [MAX_SIDECAR, MAX_SIDECAR + 1] {
            let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
            gz.write_all(&vec![b'x'; size as usize]).unwrap();
            fs::write(efm.join("1.dat"), gz.finish().unwrap()).unwrap();
            let sidecars = Sidecars::open(&dir);
            let got = sidecars.resolve("~/Files/S0/3/EFMData/1.dat");
            if size == MAX_SIDECAR { assert_eq!(got.unwrap().len(), size as usize); }
            else { assert_eq!(got.err(), Some("oversized")); }
        }
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn gzip_members_and_footer_must_reach_eof() {
        let dir = std::env::temp_dir().join(format!("hxprobe-eof-{}", std::process::id()));
        let efm = dir.join("S0/3/EFMData");
        fs::create_dir_all(&efm).unwrap();
        let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
        gz.write_all(b"first").unwrap();
        let compressed = gz.finish().unwrap();
        fs::write(efm.join("1.dat"), [&compressed[..], &compressed[..]].concat()).unwrap();
        let sidecars = Sidecars::open(&dir);
        assert_eq!(sidecars.resolve("~/Files/S0/3/EFMData/1.dat").unwrap(), "firstfirst");
        fs::write(efm.join("1.dat"), &compressed[..compressed.len() - 1]).unwrap();
        assert_eq!(sidecars.resolve("~/Files/S0/3/EFMData/1.dat").err(), Some("sidecar_decode_failed"));
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn html_becomes_text_without_markup_and_keeps_unicode() {
        let html = "<html><head><style>p{}</style></head><body><p>Caf\u{e9} &amp; tour</p><div>Thursday \u{2013} ten</div></body></html>";
        assert_eq!(html_to_text(html), "Caf\u{e9} & tour\nThursday \u{2013} ten");
    }

    #[test]
    fn paragraphs_and_breaks_keep_their_lines() {
        let html = "<p>Hi Sam,</p><p>Call <b>Pat</b> at <a href=\"x\">the office</a>.<br/>Bring\r\n the keys.   </p>";
        assert_eq!(html_to_text(html), "Hi Sam,\nCall Pat at the office.\nBring the keys.");
    }

    #[test]
    fn blank_lines_fold_to_one() {
        let outlook = "<p class=MsoNormal>Thanks,<o:p></o:p></p>\r\n<p class=MsoNormal><o:p>&nbsp;</o:p></p>\r\n\
                       <p class=MsoNormal>\u{a0}</p><p class=MsoNormal>Pat\r\nExample</p>";
        assert_eq!(html_to_text(outlook), "Thanks,\n\nPat Example");
        assert_eq!(html_to_text("<div><div>a</div></div><br><br><br><div>b</div>"), "a\n\nb");
    }

    #[test]
    fn table_signature_puts_each_cell_on_its_own_line() {
        let html = "<p>Best,</p><table><tr><td>Pat Example</td></tr>\n<tr><td>Broker | Example Realty</td></tr>\
                    <tr><td>555-0100</td><td>pat@example.com</td></tr></table>";
        assert_eq!(html_to_text(html), "Best,\nPat Example\nBroker | Example Realty\n555-0100\npat@example.com");
    }

    #[test]
    fn outlook_reply_header_starts_on_its_own_lines() {
        let html = "<div>See you then.</div><hr style=\"display:inline-block;width:98%\"><div id=\"divRplyFwdMsg\">\
                    <font face=\"Calibri\"><b>From:</b> Sam Example &lt;sam@example.com&gt;<br><b>Sent:</b> Monday, \
                    November 2, 2026 9:00 AM<br><b>To:</b> Pat Example<br><b>Subject:</b> Tour</font><div>&nbsp;</div>\
                    </div><div>Does Thursday work?</div>";
        assert_eq!(html_to_text(html), "See you then.\nFrom: Sam Example <sam@example.com>\n\
            Sent: Monday, November 2, 2026 9:00 AM\nTo: Pat Example\nSubject: Tour\n\nDoes Thursday work?");
    }

    #[test]
    fn nested_quotes_keep_their_lines() {
        let html = "<div dir=\"ltr\">Sounds good.</div><br><div class=\"gmail_quote\"><div>On Mon, Nov 2, 2026 \
                    Sam wrote:<br></div><blockquote><div>Can we meet?<br><br>Sam</div><blockquote><div>Original \
                    note</div></blockquote></blockquote></div>";
        assert_eq!(html_to_text(html), "Sounds good.\n\nOn Mon, Nov 2, 2026 Sam wrote:\nCan we meet?\n\nSam\nOriginal note");
    }

    #[test]
    fn only_indexed_sidecars_are_read() {
        let dir = std::env::temp_dir().join(format!("hxprobe-efm-{}", std::process::id()));
        let efm = dir.join("S0").join("845").join("EFMData");
        fs::create_dir_all(&efm).unwrap();
        let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        gz.write_all(b"<html><body>Full text.</body></html>").unwrap();
        fs::write(efm.join("1111.dat"), gz.finish().unwrap()).unwrap();
        let sidecars = Sidecars::open(&dir);
        assert_eq!(sidecars.len(), 1, "the S0 number is not hard-coded");
        assert!(sidecars.resolve("~/Files/S0/845/EFMData/1111.dat").unwrap().contains("Full text"));
        assert_eq!(sidecars.resolve("~/Files/S0/845/EFMData/../../../../etc/passwd"), Err("sidecar_missing"));
        assert_eq!(sidecars.resolve("~/Files/S0/845/EFMData/2222.dat"), Err("sidecar_missing"));
        fs::remove_dir_all(&dir).unwrap();
    }
}
