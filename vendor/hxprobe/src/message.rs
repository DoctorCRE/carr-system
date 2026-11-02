//! Mail messages read from object slots: one message per class 00c9 key.
//!
//! Every field is a fixed slot of the object grammar in `object.rs`. The store is a log, so a key
//! has many revisions; each field comes from the newest revision (by last-modified time, +366)
//! that sets it, and revisions tied at that time must agree. A revision whose last-modified time
//! is not a plausible date (the DateTime.MaxValue an outgoing message carries while it waits in
//! the Outbox) ranks below every dated one. Children link by key: recipients
//! (class 0055) are owned by the message key, and the body object (class 00ca) shares it. Folder
//! view rows (class 004f) repeat display copies of these fields and are never messages. Offsets
//! and their evidence are in spec/outlook/hxstore-atlas.json.

use std::collections::{BTreeMap, BTreeSet};

use super::folder::envelopes;
use super::object::{text, unix, Object};

pub(crate) const MESSAGE: (&[u16], u16) = (&[0x060f], 0x00c9);
pub(crate) const RECIPIENT: (&[u16], u16) = (&[0x015e], 0x0055);
pub(crate) const BODY: (&[u16], u16) = (&[0x074e], 0x00ca);

/// Reference kind of a message's identity header; its owner is the account.
const MESSAGE_KIND: u32 = 212;
const FOLDER_KIND: u32 = 2;

/// Class 00c9 slots.
const RECEIVED: usize = 278;
const MODIFIED: usize = 366;
const SENT: usize = 718;
const FOLDER: usize = 888;
const CONVERSATION_INDEX: usize = 1166;
const IN_REPLY_TO: usize = 1202;
const MESSAGE_ID: usize = 1218;
const ITEM_CLASS: usize = 1226;
const PREVIEW: usize = 1294;
const SENDER_NAME: usize = 1378;
const SENDER: usize = 1386;
const SUBJECT: usize = 1422;

/// Class 0055 slots: display name, then address.
const RECIPIENT_NAME: usize = 282;
const RECIPIENT_ADDRESS: usize = 290;

/// Class 00ca: the owning message reference and the body.
const BODY_OWNER: usize = 48;
const BODY_HTML: usize = 1650;

pub const TO: u32 = 204;
pub const CC: u32 = 205;
/// Only the sender's own copy (Drafts, Outbox, Sent Items) carries Bcc recipients.
pub const BCC: u32 = 206;

/// The list a recipient kind belongs to; unproven kinds stay unnamed.
pub fn kind_name(code: u32) -> Option<&'static str> {
    match code {
        TO => Some("to"),
        CC => Some("cc"),
        BCC => Some("bcc"),
        _ => None,
    }
}

fn fixed((tags, _): (&[u16], u16)) -> usize {
    tags[0] as usize - 10
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Recipient {
    pub kind: u32,
    pub address: String,
    pub name: String,
}

/// A body as stored: inline HTML, or the profile-relative path of a gzip sidecar.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum Body {
    Html(String),
    Sidecar(String),
}

#[derive(Debug, Clone, Default)]
struct Revision {
    /// .NET ticks, or 0 when the stored value is not a plausible date.
    modified: u64,
    block: usize,
    account: u64,
    folder: Option<u64>,
    sent: Option<i64>,
    received: Option<i64>,
    item_class: Option<String>,
    conversation_index: Option<Vec<u8>>,
    in_reply_to: Option<String>,
    message_id: Option<String>,
    subject: Option<String>,
    preview: Option<String>,
    sender: Option<String>,
    sender_name: Option<String>,
}

/// One message: the newest stored value of each field. `None` is unset or conflicting.
#[derive(Debug, PartialEq, Eq, Default)]
pub struct Message {
    pub block: usize,
    pub revisions: usize,
    pub account: Option<u64>,
    pub folder: Option<u64>,
    pub item_class: Option<String>,
    pub sent_unix: Option<i64>,
    pub received_unix: Option<i64>,
    /// The GUID of the ConversationIndex header: the thread identity.
    pub conversation_id: Option<[u8; 16]>,
    /// Reply depth: five-byte child blocks after the 22-byte header.
    pub conversation_depth: Option<usize>,
    pub in_reply_to: Option<String>,
    pub message_id: Option<String>,
    /// `Some("")` when the store holds an empty subject.
    pub subject: Option<String>,
    pub preview: Option<String>,
    pub sender: Option<String>,
    pub sender_name: Option<String>,
    /// `None` when revisions of one recipient object disagree.
    pub recipients: Option<Vec<Recipient>>,
    /// Stored bodies, best first: the longest inline HTML, then sidecar paths.
    pub bodies: Vec<Body>,
    pub body_objects: usize,
    pub body_rejected: usize,
    pub recipient_objects: usize,
    pub recipient_children: usize,
    pub recipient_rejected: usize,
}

#[derive(Default)]
pub struct Index {
    /// Every key named by a message identity header, accepted or not.
    observed: BTreeSet<u64>,
    revisions: BTreeMap<u64, Vec<Revision>>,
    recipients: BTreeMap<u64, BTreeMap<u64, BTreeSet<RecipientKey>>>,
    bodies: BTreeMap<u64, BTreeSet<Body>>,
    body_objects: BTreeMap<u64, usize>,
    body_rejected: BTreeMap<u64, usize>,
    recipient_objects: BTreeMap<u64, BTreeSet<u64>>,
    recipient_rejected: BTreeMap<u64, usize>,
    pub unlinked_bodies: usize,
    pub unlinked_recipients: usize,
}

/// Kind, address and display name; the name is unset until Outlook resolves the address.
type RecipientKey = (u32, String, Option<String>);

/// One revision of a message object, or `None` when it does not follow the layout.
pub(crate) fn is_revision(data: &[u8]) -> bool {
    revision(0, data).is_some_and(|(_, r)| r.item_class.is_some())
}

fn revision(block: usize, data: &[u8]) -> Option<(u64, Revision)> {
    let o = Object::new(data, fixed(MESSAGE))?;
    let (key, account) = o.reference(0, MESSAGE.1, MESSAGE_KIND)?;
    // A set slot must fit the object; read every slot first so a bad one rejects the revision.
    let slots: BTreeMap<usize, Option<&[u8]>> = [CONVERSATION_INDEX, IN_REPLY_TO, MESSAGE_ID, ITEM_CLASS, PREVIEW,
                                                 SENDER_NAME, SENDER, SUBJECT]
        .into_iter()
        .map(|at| Some((at, o.slot(at)?)))
        .collect::<Option<_>>()?;
    let string = |at| slots[&at].and_then(text);
    let index = slots[&CONVERSATION_INDEX]
        .filter(|v| v.len() >= 22 && (v.len() - 22) % 5 == 0 && v[0] == 1)
        .map(<[u8]>::to_vec);
    Some((key, Revision {
        modified: o.u64(MODIFIED).filter(|&t| unix(t).is_some()).unwrap_or(0),
        block,
        account,
        folder: o.reference(FOLDER, 0x004d, FOLDER_KIND).filter(|&(_, owner)| owner == account).map(|(f, _)| f),
        sent: o.u64(SENT).and_then(unix),
        received: o.u64(RECEIVED).and_then(unix),
        item_class: string(ITEM_CLASS),
        conversation_index: index,
        in_reply_to: string(IN_REPLY_TO).map(|s| s.trim_matches(['<', '>']).to_string()),
        message_id: string(MESSAGE_ID).map(|s| s.trim_matches(['<', '>']).to_string()),
        subject: string(SUBJECT),
        preview: string(PREVIEW),
        sender: string(SENDER),
        sender_name: string(SENDER_NAME),
    }))
}

fn recipient(data: &[u8]) -> Option<(u64, u64, RecipientKey)> {
    let o = Object::new(data, fixed(RECIPIENT))?;
    let kind = super::object::u32_at(data, 18)?;
    let (id, owner) = o.reference(0, RECIPIENT.1, kind)?;
    let read = |at| o.slot(at).flatten().and_then(text);
    let address = read(RECIPIENT_ADDRESS).filter(|a| a.contains('@'))?;
    Some((owner, id, (kind, address, read(RECIPIENT_NAME))))
}

fn body(data: &[u8]) -> Option<(u64, Body)> {
    let o = Object::new(data, fixed(BODY))?;
    let key = o.u64(10).filter(|&k| k != 0 && o.u64(30) == Some(k) && o.u64(38) == Some(k))?;
    let (owner, _) = o.reference(BODY_OWNER, MESSAGE.1, MESSAGE_KIND).filter(|&(owner, _)| owner == key)?;
    let value = o.slot(BODY_HTML).flatten().filter(|v| !v.is_empty())?;
    let body = if o.external(BODY_HTML) {
        Body::Sidecar(text(value).filter(|p| p.starts_with("~/Files/"))?)
    } else {
        Body::Html(String::from_utf8_lossy(value).into_owned())
    };
    Some((owner, body))
}

/// The newest value of a field. Revisions tied at the newest time that sets it must agree.
fn newest<T: Clone + Ord>(revs: &[Revision], get: impl Fn(&Revision) -> Option<T>) -> Option<T> {
    let mut best: Option<(u64, BTreeSet<T>)> = None;
    for r in revs {
        let Some(value) = get(r) else { continue };
        match &mut best {
            Some((time, values)) if *time == r.modified => {
                values.insert(value);
            }
            Some((time, _)) if *time > r.modified => {}
            _ => best = Some((r.modified, BTreeSet::from([value]))),
        }
    }
    best.and_then(|(_, values)| (values.len() == 1).then(|| values.into_iter().next().unwrap()))
}

/// The current value: every revision tied at the newest time must hold the same set value.
/// Unlike `newest`, an unset or invalid current value is not filled from an older revision.
fn current<T: Clone + Ord>(revs: &[Revision], get: impl Fn(&Revision) -> Option<T>) -> Option<T> {
    let latest = revs.iter().map(|r| r.modified).max()?;
    let values: Option<BTreeSet<T>> = revs.iter().filter(|r| r.modified == latest).map(get).collect();
    values.filter(|v| v.len() == 1).and_then(|v| v.into_iter().next())
}

/// One value all revisions agree on, or `None`.
fn agreed<T: Clone + Ord>(values: impl Iterator<Item = T>) -> Option<T> {
    let set: BTreeSet<T> = values.collect();
    (set.len() == 1).then(|| set.into_iter().next().unwrap())
}

impl Index {
    pub fn observe(&mut self, block: usize, data: &[u8]) {
        for (_, object) in envelopes(data, MESSAGE) {
            if let Some((key, _)) = super::object::reference(object, MESSAGE.1, MESSAGE_KIND) {
                self.observed.insert(key);
            }
            if let Some((key, r)) = revision(block, object) {
                self.revisions.entry(key).or_default().push(r);
            }
        }
        for (_, object) in envelopes(data, RECIPIENT) {
            let kind = super::object::u32_at(object, 18).unwrap_or(0);
            let identity = super::object::reference(object, RECIPIENT.1, kind);
            let decoded = recipient(object);
            if let Some((id, owner)) = identity {
                self.recipient_objects.entry(owner).or_default().insert(id);
                if decoded.is_none() {
                    *self.recipient_rejected.entry(owner).or_default() += 1;
                }
            } else {
                self.unlinked_recipients += 1;
            }
            if let Some((owner, id, value)) = decoded {
                self.recipients.entry(owner).or_default().entry(id).or_default().insert(value);
            }
        }
        for (_, object) in envelopes(data, BODY) {
            let decoded = body(object);
            let owner = object.get(BODY_OWNER..)
                .and_then(|v| super::object::reference(v, MESSAGE.1, MESSAGE_KIND)).map(|(k, _)| k);
            if let Some(owner) = owner {
                *self.body_objects.entry(owner).or_default() += 1;
                if decoded.is_none() {
                    *self.body_rejected.entry(owner).or_default() += 1;
                }
            } else {
                self.unlinked_bodies += 1;
            }
            if let Some((key, b)) = decoded {
                self.bodies.entry(key).or_default().insert(b);
            }
        }
    }

    /// Keys whose every revision broke the layout, plus decoded keys with no readable ItemClass:
    /// mail the decoder cannot place, which must count against coverage rather than vanish.
    pub fn rejected(&self, messages: &BTreeMap<u64, Message>) -> usize {
        self.observed.iter().filter(|k| messages.get(k).is_none_or(|m| m.item_class.is_none())).count()
    }

    pub fn messages(&self) -> BTreeMap<u64, Message> {
        self.revisions
            .iter()
            .map(|(&key, revs)| {
                let index = newest(revs, |r| r.conversation_index.clone());
                let latest = revs.iter().max_by_key(|r| (r.modified, std::cmp::Reverse(r.block))).unwrap();
                let recipients = match self.recipients.get(&key) {
                    None => Some(Vec::new()),
                    // Object keys give the order within each kind. Revisions must agree on kind
                    // and address, and on the name wherever one is set.
                    Some(by_id) => by_id
                        .values()
                        .map(|v| {
                            let (kind, address) = agreed(v.iter().map(|(k, a, _)| (*k, a.clone())))?;
                            let name = agreed(v.iter().filter_map(|(_, _, n)| n.clone()));
                            (name.is_some() || v.iter().all(|(_, _, n)| n.is_none()))
                                .then(|| Recipient { kind, address, name: name.unwrap_or_default() })
                        })
                        .collect(),
                };
                let mut bodies: Vec<Body> = self.bodies.get(&key).into_iter().flatten().cloned().collect();
                bodies.sort_by_key(|b| match b {
                    Body::Html(h) => (0, std::cmp::Reverse(h.len())),
                    Body::Sidecar(_) => (1, std::cmp::Reverse(0)),
                });
                let message = Message {
                    block: latest.block,
                    revisions: revs.len(),
                    account: newest(revs, |r| Some(r.account)),
                    folder: current(revs, |r| r.folder),
                    item_class: newest(revs, |r| r.item_class.clone()),
                    sent_unix: newest(revs, |r| r.sent),
                    received_unix: newest(revs, |r| r.received),
                    conversation_id: index.as_ref().map(|i| i[6..22].try_into().unwrap()),
                    conversation_depth: index.as_ref().map(|i| (i.len() - 22) / 5),
                    in_reply_to: newest(revs, |r| r.in_reply_to.clone()),
                    message_id: newest(revs, |r| r.message_id.clone()),
                    subject: newest(revs, |r| r.subject.clone()),
                    preview: newest(revs, |r| r.preview.clone()),
                    sender: newest(revs, |r| r.sender.clone()),
                    sender_name: newest(revs, |r| r.sender_name.clone()),
                    recipients,
                    bodies,
                    body_objects: self.body_objects.get(&key).copied().unwrap_or(0),
                    body_rejected: self.body_rejected.get(&key).copied().unwrap_or(0),
                    recipient_objects: self.recipient_objects.get(&key).map_or(0, |v| v.len()),
                    recipient_children: self.recipients.get(&key).map_or(0, |v| v.len()),
                    recipient_rejected: self.recipient_rejected.get(&key).copied().unwrap_or(0),
                };
                (key, message)
            })
            .collect()
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    fn put(b: &mut [u8], at: usize, bytes: &[u8]) {
        b[at..at + bytes.len()].copy_from_slice(bytes);
    }

    fn header(b: &mut [u8], class: u16, key: u64, kind: u32, owner: u64) {
        put(b, 0, &class.to_le_bytes());
        put(b, 10, &key.to_le_bytes());
        put(b, 18, &kind.to_le_bytes());
        put(b, 22, &owner.to_le_bytes());
        put(b, 30, &key.to_le_bytes());
        put(b, 38, &owner.to_le_bytes());
    }

    fn utf16(s: &str) -> Vec<u8> {
        s.encode_utf16().chain([0]).flat_map(u16::to_le_bytes).collect()
    }

    /// DateTime.MaxValue, the last-modified time of a message waiting in the Outbox.
    const OUTBOX: u64 = 3_155_378_975_999_999_999;

    /// A synthetic class 00c9 object, last modified `modified` seconds after 2026-10-30 00:00 UTC
    /// (or at `OUTBOX`), with an inline region and the given slot values.
    pub(crate) fn message(key: u64, modified: u64, fields: &[(usize, Vec<u8>)]) -> Vec<u8> {
        let mut b = vec![0; 1541];
        header(&mut b, 0x00c9, key, MESSAGE_KIND, 7);
        put(&mut b, 94, &9u16.to_le_bytes());
        let ticks = if modified == OUTBOX { OUTBOX } else { 639_000_000_000_000_000 + modified * 10_000_000 };
        put(&mut b, MODIFIED, &ticks.to_le_bytes());
        let mut heap: Vec<u8> = Vec::new();
        for (at, value) in fields {
            put(&mut b, *at, &(heap.len() as u32).to_le_bytes());
            put(&mut b, *at + 4, &(value.len() as u32 | 0x8000_0000).to_le_bytes());
            heap.extend(value);
        }
        b.extend([0xee; 9]);
        b.extend(heap);
        b
    }

    fn envelope(tag: u16, object: &[u8]) -> Vec<u8> {
        let mut v = vec![5, 0];
        v.extend(tag.to_le_bytes());
        v.extend(((object.len() + 10) as u32).to_le_bytes());
        v.extend([0, 0]);
        v.extend(object);
        v
    }

    #[test]
    fn each_field_comes_from_the_newest_revision_that_sets_it() {
        let draft = message(70, 10, &[(SUBJECT, utf16("Draft")), (SENDER, utf16("a@example.test"))]);
        let sent = message(70, 20, &[(SUBJECT, utf16("Final"))]);
        let mut index = Index::default();
        index.observe(0, &[envelope(0x060f, &draft), envelope(0x060f, &sent)].concat());
        let m = &index.messages()[&70];
        assert_eq!((m.subject.as_deref(), m.sender.as_deref(), m.revisions), (Some("Final"), Some("a@example.test"), 2));
    }

    #[test]
    fn tied_revisions_that_disagree_leave_the_field_unknown() {
        let a = message(70, 10, &[(SENDER, utf16("a@example.test"))]);
        let b = message(70, 10, &[(SENDER, utf16("b@example.test"))]);
        let mut index = Index::default();
        index.observe(0, &[envelope(0x060f, &a), envelope(0x060f, &b)].concat());
        assert_eq!(index.messages()[&70].sender, None);
    }

    #[test]
    fn an_outbox_revision_ranks_below_every_dated_one() {
        let sent = message(70, 20, &[(SUBJECT, utf16("Sent"))]);
        let outbox = message(70, OUTBOX, &[(SUBJECT, utf16("Outbox"))]);
        let mut index = Index::default();
        index.observe(0, &[envelope(0x060f, &outbox), envelope(0x060f, &sent)].concat());
        assert_eq!(index.messages()[&70].subject.as_deref(), Some("Sent"));
    }

    fn recipient(id: u64, kind: u32, address: &str, name: Option<&str>) -> Vec<u8> {
        let mut b = vec![0; 340];
        header(&mut b, 0x0055, id, kind, 70);
        let (address, name) = (utf16(address), name.map(utf16).unwrap_or_default());
        put(&mut b, RECIPIENT_ADDRESS, &0u32.to_le_bytes());
        put(&mut b, RECIPIENT_ADDRESS + 4, &(address.len() as u32 | 0x8000_0000).to_le_bytes());
        if !name.is_empty() {
            put(&mut b, RECIPIENT_NAME, &(address.len() as u32).to_le_bytes());
            put(&mut b, RECIPIENT_NAME + 4, &(name.len() as u32 | 0x8000_0000).to_le_bytes());
        }
        b.extend(address);
        b.extend(name);
        b
    }

    #[test]
    fn recipients_keep_their_kind_and_an_unresolved_name_does_not_conflict() {
        let mut index = Index::default();
        index.observe(0, &[
            envelope(0x060f, &message(70, 10, &[])),
            envelope(0x015e, &recipient(71, TO, "to@example.test", None)),
            envelope(0x015e, &recipient(71, TO, "to@example.test", Some("To"))),
            envelope(0x015e, &recipient(72, CC, "cc@example.test", None)),
            envelope(0x015e, &recipient(73, BCC, "bcc@example.test", Some("Bcc"))),
        ].concat());
        let got: Vec<_> = index.messages()[&70].recipients.clone().unwrap()
            .into_iter().map(|r| (kind_name(r.kind).unwrap(), r.address, r.name)).collect();
        assert_eq!(got, [("to", "to@example.test".into(), "To".into()), ("cc", "cc@example.test".into(), String::new()),
                         ("bcc", "bcc@example.test".into(), "Bcc".into())]);
        index.observe(0, &envelope(0x015e, &recipient(72, CC, "other@example.test", None)));
        assert_eq!(index.messages()[&70].recipients, None);
    }

    #[test]
    fn the_folder_comes_from_the_current_revision_only() {
        let inbox = |m: &mut Vec<u8>, owner: u64| header(&mut m[888..934], 0x004d, 50, FOLDER_KIND, owner);
        let mut older = message(70, 10, &[]);
        inbox(&mut older, 7);
        for newer in [message(70, 20, &[]), { let mut m = message(70, 20, &[]); inbox(&mut m, 8); m }] {
            let mut index = Index::default();
            index.observe(0, &[envelope(0x060f, &older), envelope(0x060f, &newer)].concat());
            assert_eq!(index.messages()[&70].folder, None);
        }
        let mut index = Index::default();
        index.observe(0, &envelope(0x060f, &older));
        assert_eq!(index.messages()[&70].folder, Some(50));
    }

    #[test]
    fn keys_without_a_readable_revision_or_item_class_are_counted_as_rejected() {
        let mut bad = message(71, 10, &[(SUBJECT, utf16("Subject"))]);
        put(&mut bad, SUBJECT, &0xffffu32.to_le_bytes());
        let mut index = Index::default();
        index.observe(0, &[envelope(0x060f, &message(70, 10, &[(ITEM_CLASS, utf16("IPM.Note"))])),
                           envelope(0x060f, &bad), envelope(0x060f, &message(72, 10, &[]))].concat());
        let messages = index.messages();
        assert_eq!((messages.len(), index.rejected(&messages)), (2, 2));
    }

    #[test]
    fn a_slot_outside_its_object_rejects_the_revision() {
        let mut bad = message(70, 10, &[(SUBJECT, utf16("Subject"))]);
        put(&mut bad, SUBJECT, &0xffffu32.to_le_bytes());
        let mut index = Index::default();
        index.observe(0, &envelope(0x060f, &bad));
        assert!(index.messages().is_empty());
    }

    #[test]
    fn a_body_object_names_inline_html_or_a_sidecar_path() {
        for (value, expected) in [
            (b"<html>x</html>".to_vec(), Body::Html("<html>x</html>".into())),
            (utf16("~/Files/S0/3/EFMData/1.dat"), Body::Sidecar("~/Files/S0/3/EFMData/1.dat".into())),
        ] {
            let mut b = vec![0; 1860];
            put(&mut b, 0, &0x00cau16.to_le_bytes());
            for at in [10, 30, 38] {
                put(&mut b, at, &70u64.to_le_bytes());
            }
            header(&mut b[48..94], 0x00c9, 70, MESSAGE_KIND, 7);
            let external = if matches!(expected, Body::Sidecar(_)) { 0x0200_0000 } else { 0 };
            put(&mut b, BODY_HTML + 4, &(value.len() as u32 | external | 0x8000_0000).to_le_bytes());
            b.extend(&value);
            assert_eq!(super::body(&b), Some((70, expected)));
        }
    }
}
