//! Folder objects and membership in verified HxStore block payloads.
//!
//! The serialized envelopes, repeated object keys and typed references are
//! checked before accepting a link. Names in message text are never evidence
//! of membership. See SPEC.md, "Folder objects and membership".

use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Parent {
    /// A null reference: the folder sits at the top of its account.
    Top,
    Folder(u64),
    /// No recognized reference, or cached revisions disagree.
    Unknown,
}

#[derive(Debug)]
pub struct Folder {
    pub id: u64,
    pub account_id: u64,
    /// Conflicting names in cached revisions are left unresolved.
    pub name: Option<String>,
    pub parent: Parent,
    pub block: usize,
}

/// Offset of a list-view row's message reference (2026-10 Studio build).
const VIEW_MESSAGE_AT: usize = 272;

/// Offset of the parent reference in a folder object (2026-10 Studio build).
const PARENT_AT: usize = 792;

/// A typed folder reference in the same account, or ten zero bytes for none.
fn parent(object: &[u8], account_id: u64) -> Parent {
    match object.get(PARENT_AT..PARENT_AT + 10) {
        Some(sig) if sig == [0; 10] => Parent::Top,
        Some(_) => match object.get(PARENT_AT..).and_then(|r| reference(r, 0x004d, &[2, 0x02ac])) {
            Some((id, account)) if account == account_id && id != u64_at(object, 10).unwrap_or(0) => {
                Parent::Folder(id)
            }
            _ => Parent::Unknown,
        },
        None => Parent::Unknown,
    }
}

#[derive(Default)]
pub struct Index {
    pub folders: BTreeMap<u64, Folder>,
    owners: BTreeMap<u64, BTreeSet<u64>>,
    members: BTreeMap<u64, BTreeSet<u64>>,
}

fn u64_at(data: &[u8], at: usize) -> Option<u64> {
    Some(u64::from_le_bytes(
        data.get(at..at.checked_add(8)?)?.try_into().ok()?,
    ))
}

fn u32_at(data: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_le_bytes(
        data.get(at..at.checked_add(4)?)?.try_into().ok()?,
    ))
}

fn signature(class: u16) -> [u8; 10] {
    let mut s = [0; 10];
    s[..2].copy_from_slice(&class.to_le_bytes());
    s
}

/// Envelope tags observed per object class. A tag changes between Outlook
/// builds while the class and object layout stay put: the first tag of each
/// pair is the pinned upstream build, the second the 2026-10 Studio build.
pub(crate) const FOLDER: (&[u16], u16) = (&[0x04c2, 0x04d8], 0x004d);
pub(crate) const VIEW: (&[u16], u16) = (&[0x0430], 0x004f);
pub(crate) const MESSAGE: (&[u16], u16) = (&[0x0740, 0x074e], 0x00ca);
pub(crate) const METADATA: (&[u16], u16) = (&[0x02c8, 0x02b8], 0x00bf);

/// `[05 00, tag:u16, length:u32, 00 00, object...]`.
/// Length includes the ten-byte envelope, not its enclosing transaction.
pub(crate) fn envelopes<'a>(data: &'a [u8], (tags, class): (&[u16], u16)) -> Vec<(usize, &'a [u8])> {
    let mut found: Vec<_> = tags.iter().flat_map(|&tag| tagged(data, tag, class)).collect();
    found.sort_unstable_by_key(|(start, _)| *start);
    found
}

fn tagged(data: &[u8], tag: u16, class: u16) -> Vec<(usize, &[u8])> {
    let tag_bytes = tag.to_le_bytes();
    let needle = [5, 0, tag_bytes[0], tag_bytes[1]];
    memchr::memmem::find_iter(data, &needle)
        .filter_map(|at| {
            let len = u32_at(data, at + 4)? as usize;
            if len < 56 || data.get(at + 8..at + 10)? != [0, 0] {
                return None;
            }
            let start = at + 10;
            let object = data.get(start..at.checked_add(len)?)?;
            object
                .starts_with(&signature(class))
                .then_some((start, object))
        })
        .collect()
}

/// A typed local reference repeats its own key and its owning object's key.
pub(crate) fn reference(data: &[u8], class: u16, kinds: &[u32]) -> Option<(u64, u64)> {
    if !data.starts_with(&signature(class)) || !kinds.contains(&u32_at(data, 18)?) {
        return None;
    }
    let id = u64_at(data, 10)?;
    let owner = u64_at(data, 22)?;
    (id != 0
        && id <= i64::MAX as u64
        && owner <= i64::MAX as u64
        && Some(id) == u64_at(data, 30)
        && Some(owner) == u64_at(data, 38))
    .then_some((id, owner))
}

pub(crate) fn message_key(data: &[u8], class: u16) -> Option<u64> {
    if !data.starts_with(&signature(class)) {
        return None;
    }
    let id = u64_at(data, 10)?;
    (id != 0 && Some(id) == u64_at(data, 30) && Some(id) == u64_at(data, 38)).then_some(id)
}

/// The 51-byte tail is an identity candidate, not a proven logical message ID.
pub(crate) fn metadata_tail(object: &[u8]) -> Option<&[u8; 51]> {
    let start = object.len().checked_sub(59)?;
    let tail = object.get(start..start + 51)?;
    tail.starts_with(&[0, 9, 0, 46, 0]).then(|| tail.try_into().unwrap())
}

/// Folder payloads end in the display name, following a binary sort key.
/// Decode UTF-16 (including surrogate pairs), rather than scanning ASCII runs.
pub(crate) fn trailing_name(data: &[u8]) -> Option<String> {
    if !data.ends_with(&[0, 0]) {
        return None;
    }
    let end = data.len().checked_sub(2)?;
    let mut start = end;
    while start >= 2 {
        let u = u16::from_le_bytes([data[start - 2], data[start - 1]]);
        if u < 32 || u == 127 {
            break;
        }
        start -= 2;
        if end - start > 1024 {
            return None;
        }
    }
    // The sort key has a single-byte NUL terminator before the UTF-16 name.
    if start == end || start == 0 || data[start - 1] != 0 {
        return None;
    }
    let units: Vec<_> = data[start..end]
        .chunks_exact(2)
        .map(|b| u16::from_le_bytes([b[0], b[1]]))
        .collect();
    let name = String::from_utf16(&units).ok()?;
    (!name.chars().any(char::is_control)).then_some(name)
}

impl Index {
    /// First pass: gather the catalog and message/view membership references.
    pub fn observe(&mut self, block: usize, data: &[u8]) {
        for (_, object) in envelopes(data, FOLDER) {
            let Some((id, account_id)) = reference(object, 0x004d, &[2, 0x02ac]) else {
                continue;
            };
            let Some(name) = trailing_name(object) else {
                continue;
            };
            let up = parent(object, account_id);
            let folder = self.folders.entry(id).or_insert_with(|| Folder {
                id,
                account_id,
                name: Some(name.clone()),
                parent: up,
                block,
            });
            if folder.name.as_ref() != Some(&name) || folder.account_id != account_id {
                folder.name = None;
            }
            if folder.parent != up {
                folder.parent = Parent::Unknown;
            }
            // Two copies of the folder's view-collection reference. Require
            // each reference's duplicated key, not an arbitrary nearby u64.
            for at in [160, 360] {
                if object.get(at..at + 10) != Some(signature(4).as_slice()) {
                    continue;
                }
                if let Some(owner) = u64_at(object, at + 10) {
                    if owner != 0 && Some(owner) == u64_at(object, at + 38) {
                        self.owners.entry(owner).or_default().insert(id);
                    }
                }
            }
        }
        for (_, object) in envelopes(data, METADATA) {
            let Some(key) = message_key(object, 0x00bf) else {
                continue;
            };
            if metadata_tail(object).is_none() {
                continue;
            }
            if let Some(folder) = u64_at(object, object.len() - 8) {
                self.members.entry(key).or_default().insert(folder);
            }
        }
    }

    /// Resolve IPM.Note anchor positions to observed folders within this block.
    pub fn links(&self, data: &[u8], needle: &[u8]) -> BTreeMap<usize, BTreeSet<u64>> {
        let mut links = BTreeMap::new();
        for (start, object) in envelopes(data, VIEW) {
            let Some((_, owner)) = reference(object, 0x004f, &[4]) else {
                continue;
            };
            if let Some(folders) = self.owners.get(&owner) {
                self.link_single_anchor(&mut links, start, object, needle, folders);
            }
        }
        for (start, object) in envelopes(data, MESSAGE) {
            let Some(key) = message_key(object, 0x00ca) else {
                continue;
            };
            if let Some(folders) = self.members.get(&key) {
                self.link_single_anchor(&mut links, start, object, needle, folders);
            }
        }
        // Expanded message objects embedded in transaction wrappers include
        // a full typed folder reference at +408. Only take their first
        // ItemClass anchor.
        for (start, object) in expanded(data) {
            let Some(field) = object.get(408..) else {
                continue;
            };
            let Some((id, account)) = reference(field, 0x004d, &[2, 0x02ac]) else {
                continue;
            };
            if !self
                .folders
                .get(&id)
                .is_some_and(|f| f.account_id == account)
            {
                continue;
            }
            let Some(rel) = memchr::memmem::find(object, needle) else {
                continue;
            };
            if rel < 454 {
                continue;
            }
            links
                .entry(start + rel)
                .or_insert_with(BTreeSet::new)
                .insert(id);
        }
        links
    }

    /// Resolve IPM.Note anchor positions to the local message key of the
    /// message object holding them, so other objects can name the message.
    pub fn keys(&self, data: &[u8], needle: &[u8]) -> BTreeMap<usize, u64> {
        let mut keys = BTreeMap::new();
        for (start, object) in envelopes(data, MESSAGE) {
            let Some(key) = message_key(object, MESSAGE.1) else { continue };
            let mut anchors = memchr::memmem::find_iter(object, needle);
            if let (Some(anchor), None) = (anchors.next(), anchors.next()) {
                keys.insert(start + anchor, key);
            }
        }
        // A list-view row (class 004f) has no message key of its own; its
        // typed reference at +272 (class 00c9, kind 212) names the message.
        for (start, object) in envelopes(data, VIEW) {
            let Some((key, _)) = object.get(VIEW_MESSAGE_AT..).and_then(|r| reference(r, 0x00c9, &[212])) else {
                continue;
            };
            let mut anchors = memchr::memmem::find_iter(object, needle);
            if let (Some(anchor), None) = (anchors.next(), anchors.next()) {
                keys.entry(start + anchor).or_insert(key);
            }
        }
        for (start, object) in expanded(data) {
            // Only a real message header carries its typed folder reference;
            // a bare message reference inside another object does not.
            if object.get(408..).and_then(|f| reference(f, 0x004d, &[2, 0x02ac])).is_none() {
                continue;
            }
            let Some(key) = message_key(object, MESSAGE.1) else { continue };
            if let Some(rel) = memchr::memmem::find(object, needle).filter(|&rel| rel >= 454) {
                keys.entry(start + rel).or_insert(key);
            }
        }
        keys
    }

    fn link_single_anchor(
        &self,
        links: &mut BTreeMap<usize, BTreeSet<u64>>,
        start: usize,
        object: &[u8],
        needle: &[u8],
        folders: &BTreeSet<u64>,
    ) {
        let mut anchors = memchr::memmem::find_iter(object, needle);
        let Some(anchor) = anchors.next() else { return };
        if anchors.next().is_some() {
            return;
        }
        let known: BTreeSet<_> = folders
            .iter()
            .copied()
            .filter(|id| self.folders.contains_key(id))
            .collect();
        if !known.is_empty() {
            links.entry(start + anchor).or_default().extend(known);
        }
    }
}

/// Expanded message objects have no length envelope of their own. Bound each
/// header by the next message object, the next known envelope, or 4 KiB.
fn expanded(data: &[u8]) -> Vec<(usize, &[u8])> {
    let starts: Vec<_> = memchr::memmem::find_iter(data, &signature(0x00ca))
        .filter(|&p| message_key(&data[p..], 0x00ca).is_some())
        .collect();
    let boundaries: BTreeSet<_> = [FOLDER, VIEW, MESSAGE, METADATA]
        .into_iter()
        .flat_map(|kind| envelopes(data, kind).into_iter().map(|(p, _)| p - 10))
        .collect();
    starts
        .iter()
        .enumerate()
        .filter_map(|(i, &start)| {
            let next_envelope = boundaries
                .range((std::ops::Bound::Excluded(start), std::ops::Bound::Unbounded))
                .next()
                .copied()
                .unwrap_or(data.len());
            let end = starts
                .get(i + 1)
                .copied()
                .unwrap_or(data.len())
                .min(next_envelope)
                .min(start.saturating_add(4096));
            Some((start, data.get(start..end)?))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn put64(b: &mut [u8], p: usize, v: u64) {
        b[p..p + 8].copy_from_slice(&v.to_le_bytes());
    }
    fn object(class: u16, id: u64, kind: u32, owner: u64, size: usize) -> Vec<u8> {
        let mut b = vec![0; size];
        b[..10].copy_from_slice(&signature(class));
        put64(&mut b, 10, id);
        b[18..22].copy_from_slice(&kind.to_le_bytes());
        put64(&mut b, 22, owner);
        put64(&mut b, 30, id);
        put64(&mut b, 38, owner);
        b
    }
    fn envelope(tag: u16, b: &[u8]) -> Vec<u8> {
        let mut v = vec![5, 0];
        v.extend(tag.to_le_bytes());
        v.extend(((b.len() + 10) as u32).to_le_bytes());
        v.extend([0, 0]);
        v.extend(b);
        v
    }
    fn folder(id: u64, owner: u64, name: &str) -> Vec<u8> {
        let mut b = object(0x4d, id, 2, 7, 420);
        b[160..170].copy_from_slice(&signature(4));
        put64(&mut b, 170, owner);
        put64(&mut b, 198, owner);
        b.extend([1, 1, 1, 1, 0]);
        b.extend(name.encode_utf16().chain([0]).flat_map(u16::to_le_bytes));
        envelope(0x4c2, &b)
    }
    fn note() -> Vec<u8> {
        "IPM.Note"
            .encode_utf16()
            .flat_map(u16::to_le_bytes)
            .collect()
    }

    #[test]
    fn unicode_catalog_and_bounded_view_membership() {
        let mut index = Index::default();
        index.observe(123, &folder(50, 60, "Équipe 🚀"));
        assert_eq!(index.folders[&50].name.as_deref(), Some("Équipe 🚀"));
        let mut view = object(0x4f, 70, 4, 60, 100);
        view.extend(note());
        let mut data = envelope(0x430, &view);
        let end = data.len();
        data.extend(note());
        let links = index.links(&data, &note());
        assert_eq!(links.get(&110), Some(&BTreeSet::from([50])));
        assert!(!links.contains_key(&end));
    }

    #[test]
    fn reject_truncation_bad_keys_and_unknown_owners() {
        let good = folder(50, 60, "Inbox");
        for cut in 0..good.len() {
            let mut index = Index::default();
            index.observe(0, &good[..cut]);
            assert!(index.folders.is_empty());
        }
        let mut bad = good.clone();
        bad[40] ^= 1;
        let mut index = Index::default();
        index.observe(0, &bad);
        assert!(index.folders.is_empty());
        index.observe(0, &good);
        let mut view = object(0x4f, 70, 4, 999, 100);
        view.extend(note());
        assert!(index.links(&envelope(0x430, &view), &note()).is_empty());
    }

    #[test]
    fn metadata_links_preserve_moves_and_require_id_framing() {
        let mut index = Index::default();
        index.observe(0, &folder(50, 60, "Inbox"));
        index.observe(0, &folder(51, 61, "Archive"));
        for fid in [50, 51] {
            let mut metadata = object(0xbf, 70, 0, 70, 120);
            metadata[61..66].copy_from_slice(&[0, 9, 0, 46, 0]);
            put64(&mut metadata, 112, fid);
            index.observe(0, &envelope(0x2c8, &metadata));
        }
        let mut msg = object(0xca, 70, 0, 70, 100);
        msg.extend(note());
        assert_eq!(
            index.links(&envelope(0x740, &msg), &note())[&110],
            BTreeSet::from([50, 51])
        );
        let mut bad = object(0xbf, 71, 0, 71, 120);
        put64(&mut bad, 112, 50);
        index.observe(0, &envelope(0x2c8, &bad));
        assert!(!index.members.contains_key(&71));
    }

    #[test]
    fn expanded_headers_validate_account_and_do_not_cross_next_object() {
        let mut index = Index::default();
        index.observe(0, &folder(50, 60, "Inbox"));
        let mut expanded = object(0xca, 70, 0, 70, 500);
        expanded[408..454].copy_from_slice(&object(0x4d, 50, 2, 7, 46));
        let mut next = object(0xca, 71, 0, 71, 500);
        next.extend(note());
        let mut combined = expanded.clone();
        combined.extend(next);
        assert!(index.links(&combined, &note()).is_empty());
        expanded.extend(note());
        assert_eq!(index.links(&expanded, &note())[&500], BTreeSet::from([50]));
        put64(&mut expanded, 430, 99);
        put64(&mut expanded, 446, 99);
        assert!(index.links(&expanded, &note()).is_empty());
    }

    #[test]
    fn embedded_header_cannot_claim_a_neighboring_folder_view() {
        let mut index = Index::default();
        index.observe(0, &folder(50, 60, "Inbox"));
        index.observe(0, &folder(51, 61, "Archive"));
        let mut expanded = object(0xca, 70, 0, 70, 500);
        expanded[408..454].copy_from_slice(&object(0x4d, 50, 2, 7, 46));
        let mut view = object(0x4f, 71, 4, 61, 100);
        view.extend(note());
        expanded.extend(envelope(0x430, &view));
        assert_eq!(index.links(&expanded, &note())[&610], BTreeSet::from([51]));
    }

    /// A 2026-10 build folder: envelope tag 0x04d8 and a parent slot at +792.
    fn current_folder(id: u64, account: u64, name: &str, parent: Option<(u64, u64)>) -> Vec<u8> {
        let mut b = object(0x4d, id, 2, account, 840);
        if let Some((pid, paccount)) = parent {
            b[792..838].copy_from_slice(&object(0x4d, pid, 2, paccount, 46));
        }
        b.extend([1, 1, 1, 1, 0]);
        b.extend(name.encode_utf16().chain([0]).flat_map(u16::to_le_bytes));
        envelope(0x4d8, &b)
    }

    #[test]
    fn current_build_tags_and_parent_references() {
        let mut index = Index::default();
        index.observe(0, &current_folder(50, 7, "Active Deals", None));
        index.observe(0, &current_folder(51, 7, "Deal A", Some((50, 7))));
        index.observe(0, &current_folder(52, 7, "Elsewhere", Some((50, 8))));
        index.observe(0, &current_folder(53, 7, "Self", Some((53, 7))));
        assert_eq!(index.folders[&50].parent, Parent::Top);
        assert_eq!(index.folders[&51].parent, Parent::Folder(50));
        assert_eq!(index.folders[&52].parent, Parent::Unknown);
        assert_eq!(index.folders[&53].parent, Parent::Unknown);
        // An older-layout folder has no parent slot to read.
        index.observe(0, &folder(60, 61, "Inbox"));
        assert_eq!(index.folders[&60].parent, Parent::Unknown);

        let mut metadata = object(0xbf, 70, 0, 70, 120);
        metadata[61..66].copy_from_slice(&[0, 9, 0, 46, 0]);
        put64(&mut metadata, 112, 51);
        index.observe(0, &envelope(0x2b8, &metadata));
        let mut msg = object(0xca, 70, 0, 70, 100);
        msg.extend(note());
        assert_eq!(index.links(&envelope(0x74e, &msg), &note())[&110], BTreeSet::from([51]));
    }

    #[test]
    fn anchors_resolve_to_their_message_key() {
        let index = Index::default();
        let mut compact = object(0xca, 70, 0, 70, 100);
        compact.extend(note());
        assert_eq!(index.keys(&envelope(0x74e, &compact), &note()), BTreeMap::from([(110, 70)]));
        let mut twice = compact.clone();
        twice.extend(note());
        assert!(index.keys(&envelope(0x74e, &twice), &note()).is_empty());
        let mut expanded = object(0xca, 71, 0, 71, 500);
        expanded.extend(note());
        // A bare message reference with no folder reference at +408 is not a header.
        assert!(index.keys(&expanded, &note()).is_empty());
        expanded[408..454].copy_from_slice(&object(0x4d, 50, 2, 7, 46));
        assert_eq!(index.keys(&expanded, &note()), BTreeMap::from([(500, 71)]));
    }

    #[test]
    fn moved_folder_revisions_leave_the_parent_unknown() {
        let mut index = Index::default();
        index.observe(0, &current_folder(51, 7, "Deal A", Some((50, 7))));
        index.observe(1, &current_folder(51, 7, "Deal A", None));
        assert_eq!(index.folders[&51].parent, Parent::Unknown);
        assert_eq!(index.folders[&51].name.as_deref(), Some("Deal A"));
    }

    /// An expanded message header with a typed folder reference at +408
    /// naming a folder the index has never catalogued.
    fn header(id: u64, size: usize) -> Vec<u8> {
        let mut b = object(0xca, id, 0, id, size);
        b[408..454].copy_from_slice(&object(0x4d, 50, 2, 7, 46));
        b
    }

    #[test]
    fn keys_need_a_header_reference_but_no_folder_catalog() {
        let index = Index::default();
        let mut expanded = header(70, 500);
        expanded.extend(note());
        let mut early = object(0xca, 71, 0, 71, 100);
        early.extend(note());
        let mut compact = object(0xca, 72, 0, 72, 100);
        compact.extend(note());
        let mut data = expanded.clone();
        data.extend(&early);
        data.extend(envelope(0x740, &compact));
        let keys = index.keys(&data, &note());
        assert_eq!(keys.get(&500), Some(&70));
        assert_eq!(keys.len(), 2, "an anchor inside the fixed header is not keyed");
        assert_eq!(keys.values().copied().collect::<BTreeSet<_>>(), BTreeSet::from([70, 72]));
        assert!(index.links(&data, &note()).is_empty(), "keys do not invent folder links");
        let mut bare = object(0xca, 74, 0, 74, 500);
        bare.extend(note());
        assert!(index.keys(&bare, &note()).is_empty(), "no typed folder reference, no key");
    }

    #[test]
    fn keys_never_cross_an_envelope_or_the_header_bound() {
        let index = Index::default();
        // A view envelope between the header and the next anchor ends the header.
        let mut data = header(70, 470);
        let mut view = object(0x4f, 71, 4, 60, 100);
        view.extend(note());
        data.extend(envelope(0x430, &view));
        assert!(index.keys(&data, &note()).is_empty());
        // An anchor 4096 or more bytes past the header belongs to something else.
        let mut far = header(72, 4096);
        far.extend(note());
        assert!(index.keys(&far, &note()).is_empty());
        // Only the first anchor inside one header takes its key.
        let mut twice = header(73, 500);
        twice.extend(note());
        twice.extend(vec![0; 40]);
        twice.extend(note());
        assert_eq!(index.keys(&twice, &note()), BTreeMap::from([(500, 73)]));
    }

    #[test]
    fn conflicting_folder_names_are_not_silently_overwritten() {
        let mut index = Index::default();
        index.observe(0, &folder(50, 60, "Old"));
        index.observe(1, &folder(50, 60, "New"));
        assert!(index.folders[&50].name.is_none());
    }
}
