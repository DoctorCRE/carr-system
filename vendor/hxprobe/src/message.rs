//! Message objects (class 00c9) and their recipient objects (class 0055).
//!
//! A message object has a fixed part holding `(offset, length | 0x8000_0000)`
//! descriptors at fixed object offsets, then a variable part holding the
//! values. The variable part starts after a variable-length identity string,
//! so its start is found from the `ItemClass` descriptor, whose value always
//! begins `IPM.`. Every other descriptor is checked against the object bounds.
//!
//! Each recipient is its own object, owned by the message through a typed
//! reference. Its kind says which list it belongs to; its position in that
//! list follows its object key. See spec/outlook/hxstore-atlas.json and
//! spec/outlook/recipients-threads-research.md.

use std::collections::{BTreeMap, BTreeSet};

use crate::folder;

pub(crate) const OBJECT: (&[u16], u16) = (&[0x060f], 0x00c9);
pub(crate) const RECIPIENT: (&[u16], u16) = (&[0x015e], 0x0055);

/// Offsets of the `(offset, length)` descriptor pairs in a message object.
const CONVERSATION_INDEX: usize = 1166;
const IN_REPLY_TO: usize = 1202;
const ITEM_CLASS: usize = 1226;
const SENDER: usize = 1386;
const SUBJECT: usize = 1422;
/// The variable part starts after the descriptor table.
const DESCRIPTORS_END: usize = 1430;

/// Recipient descriptors: display name, then address. Values start at +340.
const RECIPIENT_NAME: usize = 282;
const RECIPIENT_ADDRESS: usize = 290;
const RECIPIENT_VALUES: usize = 340;

pub const TO: u32 = 204;
pub const CC: u32 = 205;

/// The list a recipient kind belongs to; unproven kinds stay unnamed.
pub fn kind_name(code: u32) -> Option<&'static str> {
    match code {
        TO => Some("to"),
        CC => Some("cc"),
        _ => None,
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct Revision {
    conversation_index: Option<Vec<u8>>,
    in_reply_to: Option<String>,
    subject: Option<String>,
    sender: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Recipient {
    pub kind: u32,
    pub address: String,
    pub name: String,
}

/// What every cached revision of one message agrees on.
#[derive(Debug, PartialEq, Eq)]
pub struct Message {
    /// The GUID of the ConversationIndex header: the thread identity.
    pub conversation_id: Option<[u8; 16]>,
    /// Reply depth: five-byte child blocks after the 22-byte header.
    pub conversation_depth: Option<usize>,
    pub in_reply_to: Option<String>,
    /// `Some("")` when the store holds an empty subject.
    pub subject: Option<String>,
    pub sender: Option<String>,
    /// `None` when revisions of one recipient object disagree.
    pub recipients: Option<Vec<Recipient>>,
}

#[derive(Default)]
pub struct Index {
    revisions: BTreeMap<u64, BTreeSet<Revision>>,
    recipients: BTreeMap<u64, BTreeMap<u64, BTreeSet<RecipientKey>>>,
}

type RecipientKey = (u32, String, String);

fn u32_at(data: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_le_bytes(data.get(at..at.checked_add(4)?)?.try_into().ok()?))
}

fn u64_at(data: &[u8], at: usize) -> Option<u64> {
    Some(u64::from_le_bytes(data.get(at..at.checked_add(8)?)?.try_into().ok()?))
}

/// The `(offset, length)` at `slot`, or `None` when the slot is unset.
fn descriptor(object: &[u8], slot: usize) -> Option<(usize, usize)> {
    let len = u32_at(object, slot + 4)?;
    (len & 0x8000_0000 != 0).then(|| (u32_at(object, slot).unwrap_or(0) as usize, (len & 0x7fff_ffff) as usize))
}

fn value(object: &[u8], base: usize, (offset, len): (usize, usize)) -> Option<&[u8]> {
    let start = base.checked_add(offset)?;
    object.get(start..start.checked_add(len)?)
}

/// NUL-terminated UTF-16LE. Malformed text is a decode failure, not a value.
fn text(bytes: &[u8]) -> Option<String> {
    if bytes.len() < 2 || bytes.len() % 2 != 0 || !bytes.ends_with(&[0, 0]) {
        return None;
    }
    let units: Vec<u16> = bytes[..bytes.len() - 2].chunks_exact(2).map(|b| u16::from_le_bytes([b[0], b[1]])).collect();
    let s = String::from_utf16(&units).ok()?;
    (!s.contains('\0')).then_some(s)
}

fn base(object: &[u8]) -> Option<usize> {
    let (offset, len) = descriptor(object, ITEM_CLASS)?;
    let needle: Vec<u8> = "IPM.".encode_utf16().flat_map(u16::to_le_bytes).collect();
    let mut found = memchr::memmem::find_iter(object.get(DESCRIPTORS_END..)?, &needle)
        .map(|p| p + DESCRIPTORS_END)
        .filter_map(|p| p.checked_sub(offset))
        .filter(|&b| b >= DESCRIPTORS_END && value(object, b, (offset, len)).and_then(text).is_some());
    let b = found.next()?;
    found.next().is_none().then_some(b)
}

fn revision(object: &[u8]) -> Option<Revision> {
    let base = base(object)?;
    // A descriptor that is set must fit the object; otherwise the layout is not this one.
    let field = |slot| match descriptor(object, slot) {
        None => Some(None),
        Some(d) => value(object, base, d).map(Some),
    };
    let index = field(CONVERSATION_INDEX)?
        .filter(|v| v.len() >= 22 && (v.len() - 22) % 5 == 0 && v[0] == 1)
        .map(<[u8]>::to_vec);
    let in_reply_to = field(IN_REPLY_TO)?.map(|v| text(v).map(|s| s.trim_matches(['<', '>']).to_string()));
    let subject = field(SUBJECT)?.map(text);
    Some(Revision {
        conversation_index: index,
        in_reply_to: in_reply_to.flatten(),
        subject: subject.flatten(),
        sender: field(SENDER)?.and_then(text),
    })
}

fn recipient(object: &[u8]) -> Option<(u64, u64, RecipientKey)> {
    if object.get(..10)? != [0x55, 0, 0, 0, 0, 0, 0, 0, 0, 0] {
        return None;
    }
    let (id, kind, owner) = (u64_at(object, 10)?, u32_at(object, 18)?, u64_at(object, 22)?);
    if id == 0 || owner == 0 || u64_at(object, 30)? != id || u64_at(object, 38)? != owner {
        return None;
    }
    let read = |slot| value(object, RECIPIENT_VALUES, descriptor(object, slot)?).and_then(text);
    let address = read(RECIPIENT_ADDRESS).filter(|a| a.contains('@'))?;
    Some((owner, id, (kind, address, read(RECIPIENT_NAME).unwrap_or_default())))
}

/// One value all revisions agree on, or `None`.
fn agreed<T: Clone + Ord>(values: impl Iterator<Item = T>) -> Option<T> {
    let set: BTreeSet<T> = values.collect();
    (set.len() == 1).then(|| set.into_iter().next().unwrap())
}

impl Index {
    pub fn observe(&mut self, data: &[u8]) {
        for (_, object) in folder::envelopes(data, OBJECT) {
            let Some(key) = u64_at(object, 10).filter(|&k| k != 0 && u64_at(object, 30) == Some(k)) else {
                continue;
            };
            if let Some(r) = revision(object) {
                self.revisions.entry(key).or_default().insert(r);
            }
        }
        for (_, object) in folder::envelopes(data, RECIPIENT) {
            if let Some((owner, id, value)) = recipient(object) {
                self.recipients.entry(owner).or_default().entry(id).or_default().insert(value);
            }
        }
    }

    pub fn messages(&self) -> BTreeMap<u64, Message> {
        self.revisions
            .iter()
            .map(|(&key, revs)| {
                let index = agreed(revs.iter().map(|r| r.conversation_index.clone())).flatten();
                let recipients = match self.recipients.get(&key) {
                    None => Some(Vec::new()),
                    // Object keys give the order within each kind.
                    Some(by_id) => by_id
                        .values()
                        .map(|v| agreed(v.iter().cloned()))
                        .map(|v| v.map(|(kind, address, name)| Recipient { kind, address, name }))
                        .collect(),
                };
                let message = Message {
                    conversation_id: index.as_ref().map(|i| i[6..22].try_into().unwrap()),
                    conversation_depth: index.as_ref().map(|i| (i.len() - 22) / 5),
                    in_reply_to: agreed(revs.iter().map(|r| r.in_reply_to.clone())).flatten(),
                    subject: agreed(revs.iter().map(|r| r.subject.clone())).flatten(),
                    sender: agreed(revs.iter().map(|r| r.sender.clone())).flatten(),
                    recipients,
                };
                (key, message)
            })
            .collect()
    }
}
