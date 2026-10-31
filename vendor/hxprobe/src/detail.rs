//! Message detail objects (class 00c9, envelope tag 060f): follow-up flag,
//! pin, a one-time move to Other, and assigned categories.
//!
//! The object repeats the message's local key at +10/+30 (reference kind 0xd4,
//! owner at +22/+38), so it joins the `00ca` key exported as
//! `messages.local_keys`. Its first ~1.5 KB are a fixed
//! layout (the byte at +1491 is 3 in every object observed); strings follow.
//! Offsets were located with labelled TEST-account changes; see the store
//! atlas for the evidence and its sample size.

use std::collections::BTreeMap;

use crate::folder::{envelopes, reference};

pub(crate) const DETAIL: (&[u16], u16) = (&[0x060f], 0x00c9);
/// Reference kind at +18; the owner at +22/+38 is the account's list object.
const KIND: u32 = 0xd4;

const COMPLETED_AT: usize = 286;
const DUE_AT: usize = 294;
const MOVED_TO_OTHER_AT: usize = 1406;
const REVISION_AT: usize = 1418;
const FLAG_AT: usize = 1474;
const LAYOUT_AT: usize = 1491;
const LAYOUT: u8 = 3;
const PIN_AT: usize = 1504;
const FIXED_END: usize = 1508;
/// .NET `DateTime.MaxValue` ticks: "no date".
const NO_DATE: u64 = 0x2bca_2875_f437_3fff;
/// 2015-01-01 .. 2040-01-01 in .NET ticks.
const TICKS: std::ops::Range<u64> = 0x08d1_f36d_0530_8000..0x08f0_4a10_7b1d_8000;
const UNIX_EPOCH_TICKS: u64 = 621_355_968_000_000_000;
const MAX_CATEGORIES: u32 = 32;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Flag {
    None,
    Complete,
    Flagged,
}

impl Flag {
    pub fn as_str(self) -> &'static str {
        match self {
            Flag::None => "none",
            Flag::Complete => "complete",
            Flag::Flagged => "flagged",
        }
    }
}

/// Decoded fields of one message; `None` means unknown (absent or conflicting).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Detail {
    pub flag: Option<Flag>,
    /// Unix seconds; `Some(None)` is a known "no date".
    pub due_unix: Option<Option<i64>>,
    pub completed_unix: Option<Option<i64>>,
    pub moved_to_other: Option<bool>,
    pub pinned: Option<bool>,
    /// Candidate category names; the caller keeps only defined categories.
    pub categories: Option<Vec<String>>,
}

#[derive(Default)]
pub struct Index {
    /// Newest revision per message key.
    pub details: BTreeMap<u64, (u32, Detail)>,
    pub objects: usize,
    pub rejected: usize,
}

fn date(object: &[u8], at: usize) -> Option<Option<i64>> {
    let v = u64::from_le_bytes(object.get(at..at + 8)?.try_into().ok()?);
    if v == NO_DATE {
        return Some(None);
    }
    TICKS
        .contains(&v)
        .then(|| Some(((v - UNIX_EPOCH_TICKS) / 10_000_000) as i64))
}

/// `u32 count, then count x (u8 byte length, UTF-16LE name)`, ending the object.
fn categories(object: &[u8]) -> Vec<String> {
    let low = FIXED_END.max(object.len().saturating_sub(4096));
    for p in (low..object.len().saturating_sub(4)).rev() {
        if let Some(names) = category_list(&object[p..]) {
            return names;
        }
    }
    Vec::new()
}

fn category_list(tail: &[u8]) -> Option<Vec<String>> {
    let n = u32::from_le_bytes(tail.get(..4)?.try_into().ok()?);
    if !(1..=MAX_CATEGORIES).contains(&n) {
        return None;
    }
    let mut at = 4;
    let mut names = Vec::new();
    for _ in 0..n {
        let len = *tail.get(at)? as usize;
        let bytes = tail.get(at + 1..at + 1 + len)?;
        if len == 0 || len % 2 != 0 {
            return None;
        }
        let units: Vec<u16> = bytes.chunks_exact(2).map(|b| u16::from_le_bytes([b[0], b[1]])).collect();
        let name = String::from_utf16(&units).ok()?;
        if name.chars().any(char::is_control) {
            return None;
        }
        names.push(name);
        at += 1 + len;
    }
    (at == tail.len()).then_some(names)
}

fn parse(object: &[u8]) -> Option<(u64, u32, Detail)> {
    if object.len() < FIXED_END || object[LAYOUT_AT] != LAYOUT {
        return None;
    }
    let (key, _) = reference(object, DETAIL.1, &[KIND])?;
    let revision = u32::from_le_bytes(object[REVISION_AT..REVISION_AT + 4].try_into().ok()?);
    let flag = match object[FLAG_AT] {
        0 => Some(Flag::None),
        1 => Some(Flag::Complete),
        2 => Some(Flag::Flagged),
        _ => None,
    };
    let moved_to_other = match object[MOVED_TO_OTHER_AT] {
        0 => Some(false),
        1 => Some(true),
        _ => None,
    };
    let detail = Detail {
        flag,
        due_unix: date(object, DUE_AT),
        completed_unix: date(object, COMPLETED_AT),
        moved_to_other,
        pinned: Some(object[PIN_AT] & 1 == 1),
        categories: Some(categories(object)),
    };
    Some((key, revision, detail))
}

fn agree<T: PartialEq>(held: &mut Option<T>, new: Option<T>) {
    if *held != new {
        *held = None;
    }
}

impl Index {
    pub fn observe(&mut self, data: &[u8]) {
        for (_, object) in envelopes(data, DETAIL) {
            if crate::message::is_revision(object) { continue; }
            self.objects += 1;
            let Some((key, revision, new)) = parse(object) else { self.rejected += 1; continue };
            match self.details.get_mut(&key) {
                Some((held_rev, _)) if *held_rev > revision => {}
                Some((held_rev, held)) if *held_rev == revision => {
                    agree(&mut held.flag, new.flag);
                    agree(&mut held.due_unix, new.due_unix);
                    agree(&mut held.completed_unix, new.completed_unix);
                    agree(&mut held.moved_to_other, new.moved_to_other);
                    agree(&mut held.pinned, new.pinned);
                    agree(&mut held.categories, new.categories);
                }
                _ => {
                    self.details.insert(key, (revision, new));
                }
            }
        }
    }

    /// The decoded detail for a message's keys, if exactly one key has one.
    pub fn get(&self, key: u64) -> Option<&Detail> {
        self.details.get(&key).map(|(_, d)| d)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn put64(b: &mut [u8], p: usize, v: u64) {
        b[p..p + 8].copy_from_slice(&v.to_le_bytes());
    }

    struct Spec<'a> {
        key: u64,
        revision: u32,
        flag: u8,
        due: u64,
        completed: u64,
        other: u8,
        pin: u8,
        categories: &'a [&'a str],
    }

    const BASE: Spec<'static> = Spec {
        key: 70,
        revision: 1,
        flag: 0,
        due: NO_DATE,
        completed: NO_DATE,
        other: 0,
        pin: 0x82,
        categories: &[],
    };

    fn detail(s: &Spec) -> Vec<u8> {
        let mut b = vec![0; 1700];
        b[..2].copy_from_slice(&0xc9u16.to_le_bytes());
        for at in [10, 30] {
            put64(&mut b, at, s.key);
        }
        b[18..22].copy_from_slice(&KIND.to_le_bytes());
        for at in [22, 38] {
            put64(&mut b, at, 9);
        }
        put64(&mut b, COMPLETED_AT, s.completed);
        put64(&mut b, DUE_AT, s.due);
        put64(&mut b, DUE_AT + 8, s.due);
        b[MOVED_TO_OTHER_AT] = s.other;
        b[REVISION_AT..REVISION_AT + 4].copy_from_slice(&s.revision.to_le_bytes());
        b[FLAG_AT] = s.flag;
        b[LAYOUT_AT] = LAYOUT;
        b[PIN_AT] = s.pin;
        b.extend("Subject".encode_utf16().chain([0]).flat_map(u16::to_le_bytes));
        b.extend([1, 1, 0xdd]);
        b.extend([0x5a; 16]);
        if !s.categories.is_empty() {
            b.extend((s.categories.len() as u32).to_le_bytes());
            for c in s.categories {
                let units: Vec<u8> = c.encode_utf16().flat_map(u16::to_le_bytes).collect();
                b.push(units.len() as u8);
                b.extend(units);
            }
        }
        let mut v = vec![5, 0];
        v.extend(0x060fu16.to_le_bytes());
        v.extend(((b.len() + 10) as u32).to_le_bytes());
        v.extend([0, 0]);
        v.extend(b);
        v
    }

    fn one(index: &Index, key: u64) -> Detail {
        index.get(key).cloned().expect("detail")
    }

    /// 2026-10-29 00:00 UTC.
    const OCT29: u64 = 0x08df_354f_934e_c000;

    #[test]
    fn decodes_the_labelled_states() {
        let mut index = Index::default();
        index.observe(&detail(&Spec { key: 1, flag: 2, ..BASE }));
        index.observe(&detail(&Spec { key: 2, flag: 2, due: OCT29, pin: 0x83, ..BASE }));
        index.observe(&detail(&Spec { key: 3, flag: 1, completed: OCT29 - 864_000_000_000, ..BASE }));
        index.observe(&detail(&Spec { key: 4, other: 1, categories: &["Red category"], ..BASE }));
        assert_eq!(index.objects, 4);
        let f1 = one(&index, 1);
        assert_eq!((f1.flag, f1.due_unix, f1.pinned), (Some(Flag::Flagged), Some(None), Some(false)));
        let f2 = one(&index, 2);
        assert_eq!((f2.flag, f2.due_unix, f2.pinned), (Some(Flag::Flagged), Some(Some(1_793_232_000)), Some(true)));
        let done = one(&index, 3);
        assert_eq!((done.flag, done.completed_unix), (Some(Flag::Complete), Some(Some(1_793_145_600))));
        let other = one(&index, 4);
        assert_eq!(other.moved_to_other, Some(true));
        assert_eq!(other.categories, Some(vec!["Red category".to_string()]));
        assert_eq!(one(&index, 1).categories, Some(vec![]));
        assert_eq!(one(&index, 1).moved_to_other, Some(false));
    }

    #[test]
    fn newest_revision_wins_and_tied_disagreement_is_unknown() {
        let mut index = Index::default();
        index.observe(&detail(&Spec { revision: 5, flag: 2, ..BASE }));
        index.observe(&detail(&Spec { revision: 9, flag: 1, ..BASE }));
        index.observe(&detail(&Spec { revision: 3, flag: 0, ..BASE }));
        assert_eq!(one(&index, 70).flag, Some(Flag::Complete));
        index.observe(&detail(&Spec { revision: 9, flag: 2, pin: 0x83, ..BASE }));
        let d = one(&index, 70);
        assert_eq!((d.flag, d.pinned, d.moved_to_other), (None, None, Some(false)));
    }

    #[test]
    fn rejects_unknown_layouts_values_and_truncation() {
        let good = detail(&BASE);
        for cut in (0..good.len()).step_by(7) {
            let mut index = Index::default();
            index.observe(&good[..cut]);
            assert!(index.details.is_empty(), "cut {cut}");
        }
        let mut layout = good.clone();
        layout[10 + LAYOUT_AT] = 4;
        let mut index = Index::default();
        index.observe(&layout);
        assert!(index.details.is_empty());
        assert_eq!(index.rejected, 1);
        let mut kind = good.clone();
        kind[10 + 18] = 0xd5;
        let mut index = Index::default();
        index.observe(&kind);
        assert!(index.details.is_empty(), "another reference kind is not a detail object");
        let mut index = Index::default();
        index.observe(&detail(&Spec { flag: 3, due: 12345, other: 2, ..BASE }));
        let d = one(&index, 70);
        assert_eq!((d.flag, d.due_unix, d.moved_to_other), (None, None, None));
    }

    #[test]
    fn categories_must_end_the_object_exactly() {
        let mut index = Index::default();
        let mut bad = detail(&Spec { categories: &["Red category"], ..BASE });
        bad.truncate(bad.len() - 1);
        let len = (bad.len() as u32).to_le_bytes();
        bad[4..8].copy_from_slice(&len);
        index.observe(&bad);
        assert_eq!(one(&index, 70).categories, Some(vec![]));
        let mut index = Index::default();
        index.observe(&detail(&Spec { categories: &["Red category", "Blue category"], ..BASE }));
        assert_eq!(one(&index, 70).categories.unwrap(), ["Red category", "Blue category"]);
    }
}
