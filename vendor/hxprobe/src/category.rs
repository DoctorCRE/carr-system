//! Category definitions (class 0069) in verified HxStore block payloads.
//!
//! Each account keeps its category list as objects that repeat their own key
//! and their owner's key (+10/+30 and +22/+38), carry a colour index at +126,
//! and end with the display name after a binary sort key, like folders. The
//! colour index follows Outlook's `OlCategoryColor` numbering (1 red, 2 orange,
//! 4 yellow, 5 green, 8 blue, 9 purple for the six default categories).
//! Message assignments are read from detail objects; see `detail.rs`.

use std::collections::{BTreeMap, BTreeSet};

use crate::folder::{envelopes, reference, trailing_name};

/// Envelope tags observed for category objects (2026-10 Studio build).
pub(crate) const CATEGORY: (&[u16], u16) = (&[0x00b4], 0x0069);
const KIND: u32 = 0x2c;
const COLOR_AT: usize = 126;
/// `OlCategoryColor` runs from 0 (none) to 25.
const MAX_COLOR: u32 = 25;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Category {
    pub id: u64,
    /// Local key of the owning list object; not a folder `account_id`.
    pub owner_id: u64,
    /// None when cached revisions disagree.
    pub name: Option<String>,
    /// None when cached revisions disagree.
    pub color: Option<u32>,
}

#[derive(Default)]
pub struct Index {
    pub categories: BTreeMap<u64, Category>,
    pub objects: usize,
    conflicting: BTreeSet<u64>,
}

impl Index {
    pub fn observe(&mut self, data: &[u8]) {
        for (_, object) in envelopes(data, CATEGORY) {
            self.objects += 1;
            let Some((id, owner_id)) = reference(object, CATEGORY.1, &[KIND]) else {
                continue;
            };
            if self.conflicting.contains(&id) {
                continue;
            }
            let name = trailing_name(object);
            let color = object
                .get(COLOR_AT..COLOR_AT + 4)
                .map(|b| u32::from_le_bytes(b.try_into().unwrap()))
                .filter(|&c| c <= MAX_COLOR);
            let seen = self
                .categories
                .entry(id)
                .or_insert_with(|| Category { id, owner_id, name: name.clone(), color });
            if seen.owner_id != owner_id {
                self.categories.remove(&id);
                self.conflicting.insert(id);
                continue;
            }
            if seen.name != name {
                seen.name = None;
            }
            if seen.color != color {
                seen.color = None;
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

    fn category(id: u64, owner: u64, color: u32, name: &str) -> Vec<u8> {
        let mut b = vec![0; 170];
        b[..2].copy_from_slice(&0x69u16.to_le_bytes());
        put64(&mut b, 10, id);
        b[18..22].copy_from_slice(&KIND.to_le_bytes());
        put64(&mut b, 22, owner);
        put64(&mut b, 30, id);
        put64(&mut b, 38, owner);
        b[COLOR_AT..COLOR_AT + 4].copy_from_slice(&color.to_le_bytes());
        b.extend([0x0e, 0x8a, 0x0e, 0x21, 1, 1, 1, 1, 0]);
        b.extend(name.encode_utf16().chain([0]).flat_map(u16::to_le_bytes));
        let mut v = vec![5, 0];
        v.extend(0xb4u16.to_le_bytes());
        v.extend(((b.len() + 10) as u32).to_le_bytes());
        v.extend([0, 0]);
        v.extend(b);
        v
    }

    #[test]
    fn decodes_name_colour_and_owner() {
        let mut index = Index::default();
        let mut data = category(11, 7, 1, "Red category");
        data.extend(category(12, 7, 8, "Blue category"));
        index.observe(&data);
        assert_eq!(index.objects, 2);
        assert_eq!(
            index.categories[&11],
            Category { id: 11, owner_id: 7, name: Some("Red category".into()), color: Some(1) }
        );
        assert_eq!(index.categories[&12].color, Some(8));
    }

    #[test]
    fn disagreeing_revisions_leave_fields_unknown() {
        let mut index = Index::default();
        index.observe(&category(11, 7, 1, "Red category"));
        index.observe(&category(11, 7, 2, "Renamed"));
        assert_eq!(index.categories[&11].name, None);
        assert_eq!(index.categories[&11].color, None);
        index.observe(&category(11, 9, 1, "Red category"));
        assert!(!index.categories.contains_key(&11), "an owner conflict drops the category");
    }

    #[test]
    fn rejects_bad_framing_truncation_and_out_of_range_colour() {
        let good = category(11, 7, 1, "Red category");
        for cut in 0..good.len() {
            let mut index = Index::default();
            index.observe(&good[..cut]);
            assert!(index.categories.is_empty());
        }
        let mut bad = good.clone();
        bad[10 + 40] ^= 1; // owner repeat no longer matches
        let mut index = Index::default();
        index.observe(&bad);
        assert!(index.categories.is_empty());
        let mut index = Index::default();
        index.observe(&category(11, 7, 26, "Red category"));
        assert_eq!(index.categories[&11].color, None);
        let mut kind = good.clone();
        kind[10 + 18] = 0x2d;
        let mut index = Index::default();
        index.observe(&kind);
        assert!(index.categories.is_empty());
    }
}
