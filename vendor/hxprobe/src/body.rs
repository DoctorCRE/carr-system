//! The best body per message: EFMData sidecars and copy collapse.
//!
//! **Sidecars.** New Outlook writes some full HTML bodies outside the store, as
//! gzip files at `Files/S0/<N>/EFMData/<id>.dat` beside `HxStore.hxd`. `N`
//! differs between machines, so every `S0/*/EFMData` folder is read. A record
//! names its file with the UTF-16 string `EFMData/<id>.dat`. Records sit back
//! to back, so a name inside a record's span can belong to its neighbour. A
//! sidecar is therefore accepted only when its text contains the record's
//! own preview opening: a wrong link is rejected, never believed.
//!
//! **Copies.** The store rewrites a message on every sync, and not every
//! revision carries the send tick, so `sender + earliest tick` splits one
//! message into several rows, often one full and several preview-only. The
//! preview is the stable part: every revision of a message carries the same
//! one, while two replies in a thread do not. Messages from one sender with
//! the same subject and the same preview opening, sent within a day of each
//! other, are one message.

use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

use crate::record::{html_to_text, Record};

/// Characters of normalized text that identify a message's opening.
pub const OPENING: usize = 40;
/// Below this many normalized characters an opening identifies nothing.
const MIN_OPENING: usize = 20;
/// Copies of one message observed up to about two hours apart; replies that
/// share an opening rarely land within a day.
pub const COPY_WINDOW: i64 = 86_400;
const MAX_SIDECAR: u64 = 32 << 20;

/// Lowercase letters and digits only: markup, spacing and punctuation differ
/// between a preview and the full text it was cut from.
pub fn fingerprint(text: &str) -> String {
    text.chars()
        .flat_map(char::to_lowercase)
        .filter(|c| c.is_alphanumeric())
        .collect()
}

pub fn opening(text: &str) -> Option<String> {
    let f: String = fingerprint(text).chars().take(OPENING).collect();
    (f.chars().count() >= MIN_OPENING).then_some(f)
}

pub fn normalized_subject(subject: &str) -> String {
    let mut s = subject.trim();
    loop {
        let lower = s.to_lowercase();
        let Some(rest) = ["re:", "fw:", "fwd:", "aw:", "tr:"]
            .iter()
            .find_map(|p| lower.starts_with(p).then(|| s[p.len()..].trim_start()))
        else {
            break;
        };
        s = rest;
    }
    s.to_lowercase()
}

/// The identity one message's copies share, or None when it has too little
/// text to tell it from another message.
pub fn copy_key(r: &Record) -> Option<String> {
    let opening = opening(r.preview.as_deref().unwrap_or(&r.body))?;
    let sender = r.sender.as_deref()?;
    Some(format!(
        "{}\u{1f}{}\u{1f}{}",
        sender.to_lowercase(),
        normalized_subject(r.subject.as_deref().unwrap_or("")),
        opening
    ))
}

/// Sender and opening alone, for joining a copy whose subject differs.
pub fn sender_opening(r: &Record) -> Option<String> {
    let opening = opening(r.preview.as_deref().unwrap_or(&r.body))?;
    Some(format!("{}\u{1f}{}", r.sender.as_deref()?.to_lowercase(), opening))
}

/// `EFMData/<id>.dat` names inside a record span.
pub fn sidecar_names(span: &[u8]) -> Vec<String> {
    let needle: Vec<u8> = "EFMData/".encode_utf16().flat_map(u16::to_le_bytes).collect();
    let mut out = Vec::new();
    for at in memchr::memmem::find_iter(span, &needle) {
        let name: String = span[at + needle.len()..]
            .chunks_exact(2)
            .map(|c| u16::from_le_bytes([c[0], c[1]]))
            .take_while(|u| *u < 128 && ((*u as u8).is_ascii_alphanumeric() || *u as u8 == b'.'))
            .map(|u| u as u8 as char)
            .collect();
        if name.ends_with(".dat") && name.len() > 4 && !out.contains(&name) {
            out.push(name);
        }
    }
    out
}

#[derive(Default)]
pub struct Sidecars {
    files: HashMap<String, PathBuf>,
    cache: HashMap<String, Option<String>>,
    pub linked: usize,
    pub rejected: usize,
}

impl Sidecars {
    /// Index every `S0/*/EFMData/*.dat` under the profile's `Files` folder.
    /// Files are only read, never written; Outlook does not rewrite them.
    pub fn open(files_dir: &Path) -> Self {
        let mut files = HashMap::new();
        let roots = fs::read_dir(files_dir.join("S0")).into_iter().flatten().flatten();
        for root in roots {
            let efm = root.path().join("EFMData");
            for entry in fs::read_dir(&efm).into_iter().flatten().flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                if name.ends_with(".dat") {
                    files.insert(name, entry.path());
                }
            }
        }
        Sidecars { files, ..Default::default() }
    }

    pub fn len(&self) -> usize {
        self.files.len()
    }

    fn html(&mut self, name: &str) -> Option<String> {
        if let Some(hit) = self.cache.get(name) {
            return hit.clone();
        }
        let html = self.files.get(name).and_then(|path| {
            let file = fs::File::open(path).ok()?;
            let mut out = Vec::new();
            flate2::read::GzDecoder::new(file).take(MAX_SIDECAR).read_to_end(&mut out).ok()?;
            Some(String::from_utf8_lossy(&out).into_owned())
        });
        self.cache.insert(name.to_string(), html.clone());
        html
    }

    /// The sidecar HTML for this record, if one in its span matches it.
    pub fn body_for(&mut self, span: &[u8], r: &Record) -> Option<String> {
        let expect = opening(r.preview.as_deref().unwrap_or(&r.body))?;
        for name in sidecar_names(span) {
            let Some(html) = self.html(&name) else { continue };
            if fingerprint(&html_to_text(&html)).contains(&expect) {
                self.linked += 1;
                return Some(html);
            }
            self.rejected += 1;
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn rec(sender: &str, subject: &str, preview: &str) -> Record {
        Record {
            sender: Some(sender.into()),
            subject: Some(subject.into()),
            preview: Some(preview.into()),
            copies: 1,
            ..Default::default()
        }
    }

    fn u16le(s: &str) -> Vec<u8> {
        s.encode_utf16().flat_map(u16::to_le_bytes).collect()
    }

    #[test]
    fn copies_share_a_key_and_replies_do_not() {
        let a = rec("pat@example.test", "Re: Suite question", "Thanks, Thursday at ten works for the tour.");
        let b = rec("Pat@example.test", "Suite question", "Thanks,  Thursday at ten works for the tour!");
        let reply = rec("pat@example.test", "Re: Suite question", "One more thing about parking at the site.");
        assert_eq!(copy_key(&a), copy_key(&b), "prefix, case and spacing do not split a message");
        assert_ne!(copy_key(&a), copy_key(&reply), "a different opening is a different message");
        assert_eq!(copy_key(&rec("pat@example.test", "x", "ok")), None, "too little text to identify");
    }

    #[test]
    fn sidecar_names_are_read_from_the_span() {
        let span = [u16le("junk EFMData/1a2b.dat"), vec![0, 0], u16le("EFMData/1a2b.dat")].concat();
        assert_eq!(sidecar_names(&span), vec!["1a2b.dat".to_string()]);
    }

    #[test]
    fn a_sidecar_is_accepted_only_when_it_matches_the_preview() {
        let dir = std::env::temp_dir().join(format!("hxprobe-efm-{}", std::process::id()));
        let efm = dir.join("S0").join("845").join("EFMData");
        fs::create_dir_all(&efm).unwrap();
        for (name, html) in [
            ("1111.dat", "<html><body><p>Thanks, Thursday at ten works for the tour.</p><p>More text.</p></body></html>"),
            ("2222.dat", "<html><body><p>Someone else's message entirely, about invoices.</p></body></html>"),
        ] {
            let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
            gz.write_all(html.as_bytes()).unwrap();
            fs::write(efm.join(name), gz.finish().unwrap()).unwrap();
        }
        let mut sidecars = Sidecars::open(&dir);
        assert_eq!(sidecars.len(), 2, "the S0 number is not hard-coded");
        let r = rec("pat@example.test", "Suite", "Thanks, Thursday at ten works for the tour.");
        let wrong = u16le("EFMData/2222.dat");
        assert_eq!(sidecars.body_for(&wrong, &r), None, "a neighbour's sidecar is rejected");
        let right = [u16le("EFMData/2222.dat"), vec![0, 0], u16le("EFMData/1111.dat")].concat();
        assert!(sidecars.body_for(&right, &r).unwrap().contains("More text"), "the matching sidecar wins");
        assert_eq!((sidecars.linked, sidecars.rejected), (1, 2));
        fs::remove_dir_all(&dir).unwrap();
    }
}
