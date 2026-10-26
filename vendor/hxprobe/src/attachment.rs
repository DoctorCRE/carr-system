//! Attachment objects (class 016a) in verified HxStore block payloads.
//!
//! Each object names its message by a typed message reference at +360 and its
//! locally cached file by a profile-relative path such as
//! `~/Files/S0/<n>/Attachments/<k>/<name>[<digits>].<ext>`. Outlook writes that
//! file only once the attachment is cached, so a decoded row does not prove the
//! bytes exist. Bytes are never read here; the caller resolves the path against
//! its own private copy of the profile's Files folder.

use std::collections::{BTreeMap, BTreeSet};

use crate::folder::{envelopes, message_key};

/// Envelope tags observed for attachment objects (2026-10 Studio build).
pub(crate) const ATTACHMENT: (&[u16], u16) = (&[0x0318], 0x016a);

const MESSAGE_AT: usize = 360;
const SIZE_AT: usize = 558;
const MAX_TEXT_UNITS: usize = 1024;
const PATH_PREFIX: &str = "~/Files/S0/";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Attachment {
    pub id: u64,
    pub message_key: u64,
    /// The file name with Outlook's `[<digits>]` cache suffix removed.
    pub name: String,
    /// True when that name also appears as its own string in the object.
    pub name_verified: bool,
    /// The sender's declared MIME type, as stored; not a detected type.
    pub content_type: Option<String>,
    pub content_id: Option<String>,
    pub size: u64,
    /// Profile-relative cache path; None when cached revisions disagree.
    pub path: Option<String>,
}

#[derive(Default)]
pub struct Index {
    pub attachments: BTreeMap<u64, Attachment>,
    pub objects: usize,
    conflicting_owners: BTreeSet<u64>,
}

fn u64_at(data: &[u8], at: usize) -> Option<u64> {
    Some(u64::from_le_bytes(data.get(at..at.checked_add(8)?)?.try_into().ok()?))
}

/// The NUL-terminated UTF-16LE string starting at `at`, bounded and printable.
fn utf16z(data: &[u8], at: usize) -> Option<String> {
    let mut units = Vec::new();
    let mut p = at;
    loop {
        let u = u16::from_le_bytes([*data.get(p)?, *data.get(p + 1)?]);
        if u == 0 {
            break;
        }
        units.push(u);
        if units.len() > MAX_TEXT_UNITS {
            return None;
        }
        p += 2;
    }
    let s = String::from_utf16(&units).ok()?;
    (!s.is_empty() && !s.chars().any(char::is_control)).then_some(s)
}

fn utf16_bytes(s: &str) -> Vec<u8> {
    s.encode_utf16().flat_map(u16::to_le_bytes).collect()
}

/// `~/Files/S0/<digits>/Attachments/<digits>/<file>`, with no other segments.
fn cache_path(object: &[u8]) -> Option<(usize, String)> {
    let at = memchr::memmem::find(object, &utf16_bytes(PATH_PREFIX))?;
    let path = utf16z(object, at)?;
    let parts: Vec<_> = path.split('/').collect();
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    let ok = parts.len() == 7
        && parts[..3] == ["~", "Files", "S0"]
        && digits(parts[3])
        && parts[4] == "Attachments"
        && digits(parts[5])
        && !parts[6].is_empty()
        && parts[6] != "."
        && parts[6] != ".."
        && !parts[6].contains('\\');
    ok.then_some((at, path))
}

/// Remove the `[<digits>]` Outlook appends before the extension.
fn display_name(file: &str) -> Option<String> {
    let (stem, ext) = match file.rfind('.') {
        Some(dot) if file[dot..].find(['[', ']']).is_none() => file.split_at(dot),
        _ => (file, ""),
    };
    let open = stem.strip_suffix(']')?.rfind('[')?;
    let digits = &stem[open + 1..stem.len() - 1];
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) || open == 0 {
        return None;
    }
    let name = format!("{}{}", &stem[..open], ext);
    (name != "." && name != "..").then_some(name)
}

/// The first short ASCII `type/subtype` string between the fixed header and the path.
fn declared_type(object: &[u8], end: usize) -> Option<String> {
    let region = object.get(MESSAGE_AT + 40..end)?;
    let mut i = 0;
    while i + 2 <= region.len() {
        if let Some(s) = utf16z(region, i).filter(|s| s.len() >= 3 && s.len() <= 127) {
            let mut parts = s.splitn(2, '/');
            let (a, b) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""));
            let token = |t: &str| !t.is_empty() && t.bytes().all(|c| c.is_ascii_alphanumeric() || b".+-".contains(&c));
            if token(a) && token(b) && a.bytes().all(|c| c.is_ascii_alphabetic()) {
                return Some(s.to_ascii_lowercase());
            }
            i += 2 * (s.encode_utf16().count() + 1);
        } else {
            i += 2;
        }
    }
    None
}

pub(crate) fn parse(object: &[u8]) -> Option<Attachment> {
    if !object.starts_with(&ATTACHMENT.1.to_le_bytes()) || object.get(2..10)? != [0; 8] {
        return None;
    }
    // The object's own key repeats at +30; +38 repeats its owner instead.
    let id = u64_at(object, 10).filter(|&id| id != 0 && Some(id) == u64_at(object, 30))?;
    let message_key = message_key(object.get(MESSAGE_AT..)?, 0x00ca)?;
    let (path_at, path) = cache_path(object)?;
    let name = display_name(path.rsplit('/').next()?)?;
    let mut needle = utf16_bytes(&name);
    needle.extend([0, 0]);
    let path_end = path_at + 2 * (path.encode_utf16().count() + 1);
    let name_at = memchr::memmem::find_iter(object, &needle)
        .find(|&at| at >= path_end && at >= 5 && object[at - 5..at] == [1, 1, 1, 1, 0]);
    let name_verified = name_at.is_some();
    let content_id = name_at
        .and_then(|at| utf16z(object, at + needle.len()))
        .filter(|s| !s.chars().any(char::is_whitespace));
    Some(Attachment {
        id,
        message_key,
        name,
        name_verified,
        content_type: declared_type(object, path_at),
        content_id,
        size: u64_at(object, SIZE_AT)?,
        path: Some(path),
    })
}

impl Index {
    pub fn observe(&mut self, data: &[u8]) {
        for (_, object) in envelopes(data, ATTACHMENT) {
            self.objects += 1;
            let Some(a) = parse(object) else { continue };
            if self.conflicting_owners.contains(&a.id) {
                continue;
            }
            if self.attachments.get(&a.id).is_some_and(|prev| prev.message_key != a.message_key) {
                self.attachments.remove(&a.id);
                self.conflicting_owners.insert(a.id);
                continue;
            }
            match self.attachments.get_mut(&a.id) {
                None => {
                    self.attachments.insert(a.id, a);
                }
                Some(prev) => {
                    // Revisions agree on the message; anything else is
                    // taken conservatively rather than by block order.
                    if prev.path != a.path {
                        prev.path = None;
                    }
                    prev.size = prev.size.max(a.size);
                    prev.name_verified &= a.name_verified;
                    if prev.content_id != a.content_id {
                        prev.content_id = None;
                    }
                    if prev.content_type != a.content_type {
                        prev.content_type = None;
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn put64(b: &mut [u8], p: usize, v: u64) {
        b[p..p + 8].copy_from_slice(&v.to_le_bytes());
    }
    fn sig(class: u16) -> [u8; 10] {
        let mut s = [0; 10];
        s[..2].copy_from_slice(&class.to_le_bytes());
        s
    }
    fn text(s: &str) -> Vec<u8> {
        let mut b = utf16_bytes(s);
        b.extend([0, 0]);
        b
    }
    pub(crate) fn object(id: u64, message: u64, size: u64, file: &str, name: &str, mime: &str) -> Vec<u8> {
        let mut b = vec![0; 600];
        b[..10].copy_from_slice(&sig(0x016a));
        put64(&mut b, 10, id);
        put64(&mut b, 22, 99);
        put64(&mut b, 30, id);
        put64(&mut b, 38, 99);
        b[MESSAGE_AT..MESSAGE_AT + 10].copy_from_slice(&sig(0x00ca));
        for at in [10, 30, 38] {
            put64(&mut b, MESSAGE_AT + at, message);
        }
        put64(&mut b, SIZE_AT, size);
        b.extend(text(mime));
        b.extend([1, 0]);
        b.extend(text(&format!("~/Files/S0/42/Attachments/0/{file}")));
        b.extend([1, 1, 1, 1, 0]);
        b.extend(text(name));
        b
    }
    pub(crate) fn envelope(b: &[u8]) -> Vec<u8> {
        let mut v = vec![5, 0];
        v.extend(0x0318u16.to_le_bytes());
        v.extend(((b.len() + 10) as u32).to_le_bytes());
        v.extend([0, 0]);
        v.extend(b);
        v
    }

    #[test]
    fn decodes_message_link_name_type_size_and_cache_path() {
        let mut index = Index::default();
        index.observe(&envelope(&object(7, 70, 4096, "Lease draft[123].pdf", "Lease draft.pdf", "application/pdf")));
        let a = &index.attachments[&7];
        assert_eq!((a.message_key, a.size), (70, 4096));
        assert_eq!(a.name, "Lease draft.pdf");
        assert!(a.name_verified);
        assert_eq!(a.content_type.as_deref(), Some("application/pdf"));
        assert_eq!(a.path.as_deref(), Some("~/Files/S0/42/Attachments/0/Lease draft[123].pdf"));
    }

    #[test]
    fn content_id_follows_the_standalone_filename() {
        let mut object = object(7, 70, 4096, "image[123].png", "image.png", "image/png");
        object.extend(text("part-1@example.test"));
        let mut index = Index::default();
        index.observe(&envelope(&object));
        assert_eq!(index.attachments[&7].content_id.as_deref(), Some("part-1@example.test"));
    }

    #[test]
    fn absent_truncated_and_conflicting_content_ids_stay_unknown() {
        let base = object(7, 70, 1, "a[1].pdf", "a.pdf", "application/pdf");
        assert_eq!(parse(&base).unwrap().content_id, None);
        let mut truncated = base.clone();
        truncated.extend(utf16_bytes("part@example.test"));
        assert_eq!(parse(&truncated).unwrap().content_id, None);
        let mut index = Index::default();
        for cid in ["one@example.test", "two@example.test", "one@example.test"] {
            let mut revision = base.clone();
            revision.extend(text(cid));
            index.observe(&envelope(&revision));
        }
        assert_eq!(index.attachments[&7].content_id, None);
    }

    #[test]
    fn filename_suffix_is_not_a_content_id_field_boundary() {
        let mut object = object(7, 70, 1, "a[1].pdf", "prefixa.pdf", "application/pdf");
        object.extend(text("part@example.test"));
        assert_eq!(parse(&object).unwrap().content_id, None);
    }

    #[test]
    fn conflicting_message_owners_never_receive_attachment_metadata() {
        for owners in [[70, 71, 70], [71, 70, 71]] {
            let mut index = Index::default();
            for owner in owners {
                index.observe(&envelope(&object(7, owner, 1, "a[1].pdf", "a.pdf", "application/pdf")));
            }
            assert!(index.attachments.is_empty());
        }
    }

    #[test]
    fn cache_suffix_rules() {
        assert_eq!(display_name("a.b[1].pdf").as_deref(), Some("a.b.pdf"));
        assert_eq!(display_name("README[22]").as_deref(), Some("README"));
        assert_eq!(display_name("x[].pdf"), None);
        assert_eq!(display_name("[5].pdf"), None);
        assert_eq!(display_name("plain.pdf"), None);
        assert_eq!(display_name(".[1]"), None);
        assert_eq!(display_name("..[1]"), None);
        assert_eq!(display_name(".[1]."), None);
    }

    #[test]
    fn rejects_traversal_unlinked_and_truncated_objects() {
        for file in ["..", "a/../b[1].pdf", "a\\b[1].pdf"] {
            let mut index = Index::default();
            index.observe(&envelope(&object(7, 70, 1, file, "x", "text/plain")));
            assert!(index.attachments.is_empty(), "{file}");
        }
        let mut unlinked = object(7, 70, 1, "a[1].pdf", "a.pdf", "application/pdf");
        put64(&mut unlinked, MESSAGE_AT + 30, 71);
        let good = envelope(&object(7, 70, 1, "a[1].pdf", "a.pdf", "application/pdf"));
        let mut index = Index::default();
        index.observe(&envelope(&unlinked));
        for cut in 0..good.len() {
            index.observe(&good[..cut]);
        }
        assert!(index.attachments.is_empty());
    }

    #[test]
    fn disagreeing_revisions_drop_the_path_and_keep_the_larger_size() {
        let mut index = Index::default();
        let first = object(7, 70, 10, "a[1].pdf", "a.pdf", "application/pdf");
        let moved = object(7, 70, 20, "a[2].pdf", "a.pdf", "application/pdf");
        index.observe(&[envelope(&first), envelope(&moved)].concat());
        assert_eq!(index.attachments[&7].path, None);
        assert_eq!(index.attachments[&7].size, 20);
    }
}
