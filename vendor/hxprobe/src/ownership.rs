//! Ownership is established from bounded mailbox fields, never participants.
use std::collections::BTreeSet;
pub const VERSION: &str = "hx-owner-v1";
const MAILBOX_TAG: u16 = 0x06ca;
const IDENTITY_AT: usize = 1748;

fn u64at(d: &[u8], p: usize) -> Option<u64> {
    Some(u64::from_le_bytes(d.get(p..p + 8)?.try_into().ok()?))
}
fn key(d: &[u8]) -> Option<u64> {
    let id = u64at(d, 10)?;
    (id > 0 && id <= i64::MAX as u64 && u64at(d, 30) == Some(id)).then_some(id)
}
pub fn root_ref(d: &[u8]) -> Option<u64> {
    if d.len() != 46
        || d.get(..10)? != [0x49, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        || d.get(18..30)? != [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    {
        return None;
    }
    let id = key(d)?;
    (u64at(d, 38) == Some(u64::MAX - 1)).then_some(id)
}

/// Complete envelope bounds. No content decoding occurs at this seam.
pub fn envelopes(data: &[u8]) -> Vec<(usize, u16, &[u8])> {
    let mut out = Vec::new();
    for p in 0..data.len().saturating_sub(19) {
        if data[p..p + 2] != [5, 0] || data[p + 8..p + 10] != [0, 0] {
            continue;
        }
        let len = u32::from_le_bytes(data[p + 4..p + 8].try_into().unwrap()) as usize;
        if !(20..=1 << 20).contains(&len) {
            continue;
        }
        let Some(obj) = data.get(p + 10..p + len) else {
            continue;
        };
        if obj.get(2..10) != Some(&[0; 8]) {
            continue;
        }
        out.push((
            p + 10,
            u16::from_le_bytes(data[p + 2..p + 4].try_into().unwrap()),
            obj,
        ));
    }
    out
}

fn mailbox(obj: &[u8]) -> Option<(u64, u64, Option<&[u8]>)> {
    if obj.get(..10)? != [0x4a, 0, 0, 0, 0, 0, 0, 0, 0, 0] || obj.get(18..30)? != [0; 12] {
        return None;
    }
    let id = key(obj)?;
    if u64at(obj, 38) != Some(id) {
        return None;
    }
    let root = root_ref(obj.get(48..94)?)?;
    if root_ref(obj.get(112..158)?) != Some(root)
        || obj.windows(46).filter_map(root_ref).any(|r| r != root)
    {
        return None;
    }
    let identity = (|| {
        let p = IDENTITY_AT;
        if obj.get(p..p + 4)? != 1u32.to_le_bytes() {
            return None;
        }
        let n = *obj.get(p + 4)? as usize;
        if n == 0 || n % 2 != 0 {
            return None;
        }
        let q = p + 5 + n;
        if obj.get(q..q + 4)? != 1u32.to_le_bytes()
            || *obj.get(q + 4)? as usize != n
            || q + 5 + n != obj.len()
        {
            return None;
        }
        let text = obj.get(p + 5..q)?;
        (obj.get(q + 5..)? == text).then_some(text)
    })();
    Some((id, root, identity))
}

#[derive(Clone, Debug)]
pub struct Account {
    pub root: u64,
    pub mailbox: u64,
    pub binding_sources: Vec<(usize, usize)>,
}
impl Account {
    pub fn bind<B: AsRef<[u8]>>(
        blocks: impl IntoIterator<Item = (usize, B)>,
        identity: &str,
    ) -> Option<Self> {
        if identity.is_empty() || !identity.is_ascii() || identity.len() > 127 {
            return None;
        }
        let allowed: Vec<u8> = identity.encode_utf16().flat_map(u16::to_le_bytes).collect();
        let mut observations = Vec::new();
        let mut positives = BTreeSet::new();
        for (block, data) in blocks {
            for (at, tag, obj) in envelopes(data.as_ref()) {
                if obj[0..2] != [0x4a, 0] {
                    continue;
                }
                // An unsupported mailbox cannot establish or corroborate a binding.
                let parsed = (tag == MAILBOX_TAG).then(|| mailbox(obj)).flatten();
                let Some((id, root, text)) = parsed else {
                    return None;
                };
                let matches = text == Some(allowed.as_slice());
                if matches {
                    positives.insert((id, root));
                }
                observations.push((id, root, matches, block, at));
            }
        }
        if positives.len() != 1 {
            return None;
        }
        let (mailbox, root) = *positives.first()?;
        if observations.iter().any(|(id, r, ok, _, _)| {
            (*id == mailbox || *r == root) && (!ok || *id != mailbox || *r != root)
        }) {
            return None;
        }
        let binding_sources = observations
            .into_iter()
            .filter(|x| x.2)
            .map(|x| (x.3, x.4))
            .collect();
        Some(Self {
            root,
            mailbox,
            binding_sources,
        })
    }
    pub fn revisions<'a>(&self, data: &'a [u8]) -> Vec<(usize, &'a [u8])> {
        let frames = envelopes(data);
        frames
            .iter()
            .filter_map(|&(at, tag, obj)| {
                let class = u16::from_le_bytes(obj[..2].try_into().ok()?);
                if ![(0x430, 79), (0x60f, 201), (0x74e, 202), (0x455, 107)].contains(&(tag, class))
                {
                    return None;
                }
                if frames.iter().any(|(other, _, bytes)| {
                    *other != at
                        && [79, 107, 201, 202]
                            .contains(&u16::from_le_bytes(bytes[..2].try_into().unwrap()))
                        && other - 10 < at + obj.len()
                        && at - 10 < other + bytes.len()
                }) {
                    return None;
                }
                let id = key(obj)?;
                let kind = u32::from_le_bytes(obj.get(18..22)?.try_into().ok()?);
                let enclosing = u64at(obj, 22)?;
                let header_ok = match class {
                    79 => {
                        kind == 4
                            && enclosing > 0
                            && enclosing <= i64::MAX as u64
                            && u64at(obj, 38) == Some(enclosing)
                    }
                    201 => {
                        kind == 212
                            && enclosing > 0
                            && enclosing <= i64::MAX as u64
                            && u64at(obj, 38) == Some(enclosing)
                    }
                    202 | 107 => kind == 0 && enclosing == 0 && u64at(obj, 38) == Some(id),
                    _ => false,
                };
                if !header_ok {
                    return None;
                }
                let owner = root_ref(obj.get(112..158)?)?;
                if owner != self.root {
                    return None;
                }
                if [202, 107].contains(&class) && u64at(obj, 38) != Some(id) {
                    return None;
                }
                // Reject another complete root reference anywhere in this bounded object.
                if obj.windows(46).filter_map(root_ref).any(|r| r != owner) {
                    return None;
                }
                Some((at, obj))
            })
            .collect()
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn put(d: &mut [u8], p: usize, n: u64) {
        d[p..p + 8].copy_from_slice(&n.to_le_bytes());
    }
    fn root(id: u64) -> Vec<u8> {
        let mut d = vec![0; 46];
        d[0] = 0x49;
        put(&mut d, 10, id);
        d[18] = 1;
        put(&mut d, 30, id);
        put(&mut d, 38, u64::MAX - 1);
        d
    }
    fn frame(tag: u16, d: Vec<u8>) -> Vec<u8> {
        let mut v = vec![5, 0];
        v.extend(tag.to_le_bytes());
        v.extend(((d.len() + 10) as u32).to_le_bytes());
        v.extend([0, 0]);
        v.extend(d);
        v
    }
    fn mailbox(id: u64, owner: u64, text: &str) -> Vec<u8> {
        let mut d = vec![0; 1748];
        d[0] = 0x4a;
        for p in [10, 30, 38] {
            put(&mut d, p, id)
        }
        d[48..94].copy_from_slice(&root(owner));
        d[112..158].copy_from_slice(&root(owner));
        let b: Vec<u8> = text.encode_utf16().flat_map(u16::to_le_bytes).collect();
        for _ in 0..2 {
            d.extend(1u32.to_le_bytes());
            d.push(b.len() as u8);
            d.extend(&b)
        }
        frame(0x6ca, d)
    }
    fn revision(id: u64, owner: u64) -> Vec<u8> {
        let mut d = vec![0; 600];
        d[0] = 0xca;
        for p in [10, 30, 38] {
            put(&mut d, p, id)
        }
        d[112..158].copy_from_slice(&root(owner));
        d.extend(
            "joe.bookout@carr.us"
                .encode_utf16()
                .flat_map(u16::to_le_bytes),
        );
        frame(0x74e, d)
    }
    #[test]
    fn positive_binding_denies_unnamed_root_before_recovery() {
        let mb = mailbox(9, 7, "joe.bookout@carr.us");
        let a = Account::bind([(4096, &mb)], "joe.bookout@carr.us")
            .expect("allowed mailbox binds dynamically");
        assert_eq!((a.root, a.mailbox), (7, 9));
        let good = revision(12, 7);
        let other = revision(12, 999);
        let block = [good, other].concat();
        let owned = a.revisions(&block);
        assert_eq!(
            owned.len(),
            1,
            "an unnamed owner containing allowed participant must be denied"
        );
        assert_eq!(owned[0].0, 10);
    }
    #[test]
    fn conflicting_identity_or_root_never_binds() {
        let a = mailbox(9, 7, "joe.bookout@carr.us");
        for b in [
            mailbox(11, 7, "unnamed@example.test"),
            mailbox(9, 8, "unnamed@example.test"),
            mailbox(11, 8, "joe.bookout@carr.us"),
        ] {
            assert!(
                Account::bind([(1, &[a.clone(), b].concat())], "joe.bookout@carr.us").is_none()
            );
        }
    }
    #[test]
    fn exact_mailbox_tail_and_supported_envelope_are_required() {
        let a = mailbox(9, 7, "joe.bookout@carr.us");
        for p in [0, 2, 10, 20, 40, 48, 58, 122, a.len() - 1, a.len() - 43] {
            let mut b = a.clone();
            b[p] ^= 1;
            assert!(
                Account::bind([(1, &b)], "joe.bookout@carr.us").is_none(),
                "mutation {p}"
            );
        }
        for n in 0..a.len() {
            assert!(Account::bind([(1, &a[..n])], "joe.bookout@carr.us").is_none());
        }
        let mut b = mailbox(9, 7, "unnamed@example.test");
        b.extend(
            "joe.bookout@carr.us"
                .encode_utf16()
                .flat_map(u16::to_le_bytes),
        );
        assert!(Account::bind([(1, &b)], "joe.bookout@carr.us").is_none());
    }
    #[test]
    fn root_is_derived_for_each_allowed_identity_binding() {
        let d = mailbox(123, 456, "joe.bookout@carr.us");
        let a = Account::bind([(1, &d)], "joe.bookout@carr.us").unwrap();
        assert_eq!((a.mailbox, a.root), (123, 456));
        assert_eq!(a.revisions(&revision(3, 456)).len(), 1);
        assert!(a.revisions(&revision(3, 7)).is_empty());
    }
    #[test]
    fn nested_competing_owner_is_rejected() {
        let d = mailbox(9, 7, "joe.bookout@carr.us");
        let a = Account::bind([(1, &d)], "joe.bookout@carr.us").unwrap();
        let mut outer = revision(12, 999);
        outer.extend(revision(13, 7));
        let len = outer.len() as u32;
        outer[4..8].copy_from_slice(&len.to_le_bytes());
        assert!(
            a.revisions(&outer).is_empty(),
            "an inner allowed frame cannot escape its other-root outer envelope"
        );
    }

    #[test]
    fn competing_root_inside_mailbox_is_rejected() {
        let mut d = mailbox(9, 7, "joe.bookout@carr.us");
        d[310..356].copy_from_slice(&root(8));
        assert!(Account::bind([(1, &d)], "joe.bookout@carr.us").is_none());
    }
    #[test]
    fn revision_header_mutations_cannot_claim_an_allowed_root() {
        let d = mailbox(9, 7, "joe.bookout@carr.us");
        let a = Account::bind([(1, &d)], "joe.bookout@carr.us").unwrap();
        for offset in [28, 32] {
            let mut r = revision(12, 7);
            r[offset] ^= 1;
            assert!(a.revisions(&r).is_empty(), "header mutation {offset}");
        }
    }
}
