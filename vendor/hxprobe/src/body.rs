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

/// Strip tags and decode entities, yielding readable text.
pub fn html_to_text(html: &str) -> String {
    let mut out = String::with_capacity(html.len() / 2);
    let mut rest = html;
    let mut skip = 0usize;
    while let Some(c) = rest.chars().next() {
        if c == '<' {
            let Some(e) = rest.find('>') else { break };
            let name: String = rest[1..e]
                .chars()
                .take_while(|c| c.is_ascii_alphanumeric() || *c == '/')
                .collect::<String>()
                .to_ascii_lowercase();
            match name.as_str() {
                "script" | "style" | "head" => skip += 1,
                "/script" | "/style" | "/head" => skip = skip.saturating_sub(1),
                // Block-level closes become a space so words either side of a
                // tag do not run together.
                "br" | "/p" | "/div" | "/tr" | "/li" | "/h1" | "/h2" | "/h3" | "/td" if skip == 0 => out.push(' '),
                _ => {}
            }
            rest = &rest[e + 1..];
            continue;
        }
        if skip == 0 {
            out.push(c);
        }
        rest = &rest[c.len_utf8()..];
    }
    let out = out
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&apos;", "'")
        .replace("&mdash;", "-")
        .replace("&ndash;", "-");
    let mut text = String::with_capacity(out.len());
    let mut last_ws = false;
    for c in out.chars() {
        if c.is_control() && c != '\n' {
            continue;
        }
        if c.is_whitespace() {
            if !last_ws {
                text.push(' ');
            }
            last_ws = true;
        } else {
            text.push(c);
            last_ws = false;
        }
    }
    text.trim().to_string()
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
        assert_eq!(html_to_text(html), "Caf\u{e9} & tour Thursday \u{2013} ten");
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
