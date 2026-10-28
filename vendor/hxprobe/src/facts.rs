//! What one verified block contributes to `hxprobe db`, and a private cache of
//! it so `db --cache` decodes only the blocks that changed.
//!
//! [`extract`] reads everything `db` needs from a block that depends on that
//! block alone: catalog observations, folder-link candidates, message keys and
//! parsed records. Cross-block work (folder resolution, sidecar bodies, copy
//! merging) runs afterwards over every block's facts in file order. A full
//! decode and an incremental one therefore run the same fold over the same
//! facts; the cache only decides whether a block's facts are extracted again.
//!
//! A cache entry is keyed on the block's offset and its 32 header bytes, which
//! carry both CRC-32s, the type and both lengths. Every block is still checked
//! against its checksums on every run, so a block whose payload changed under
//! an unchanged header fails verification exactly as it does in a full decode.
//! Entries carry their own CRC-32 and the decoder's source identity; anything
//! unreadable is a miss, never an error.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

use crate::{attachment, block, body, calendar, folder, record, schema};

/// The decoder build these facts came from: a digest of its source files.
const SOURCE: &str = env!("HXPROBE_FACTS_SOURCE");
const MAGIC: &[u8; 8] = b"HXPFACT1";
/// The bytes that key a block: both checksums, magic, type and lengths.
pub const KEY: usize = 0x20;

/// One parsed message record, before cross-block resolution.
pub struct RecordFacts {
    pub anchor: usize,
    pub record: record::Record,
    pub convo_ids: Vec<String>,
    /// `EFMData` sidecar names in the record's span, when it has no inline HTML.
    pub sidecars: Vec<String>,
}

#[derive(Default)]
pub struct BlockFacts {
    pub folders: Vec<folder::Observation>,
    pub candidates: folder::Candidates,
    pub keys: BTreeMap<usize, u64>,
    pub events: Vec<calendar::Observation>,
    pub attachments: Vec<Option<attachment::Attachment>>,
    pub records: Vec<RecordFacts>,
}

/// Everything `db` reads from one decompressed block on its own.
pub fn extract(offset: usize, data: &[u8], needle: &[u8]) -> BlockFacts {
    let mut records = Vec::new();
    for loc in crate::records(data, needle) {
        let Some(mut r) = record::parse(offset, data, loc.anchor) else { continue };
        let m = schema::build_bounded(data, loc.anchor, loc.end, loc.back);

        // The named map is authoritative for every field it covers: it
        // assigns slots by their position in the run sequence and checks
        // each against the type the protocol schema says it holds.
        // `record::parse` supplies only what is not a string run -- the
        // HTML body -- plus the send time, read here from the span.
        r.sent_unix = record::read_time(loc.span);
        // Take the map's value where it has one, but keep what
        // `record::parse` found otherwise -- the two paths locate fields
        // differently, and the shape-based path still covers records the
        // map's stricter type checks reject.
        if let Some(v) = m.get(schema::Key::SenderAddress) {
            r.sender = Some(v.to_string());
        }
        if let Some(v) = m.get(schema::Key::SenderName) {
            r.sender_name = Some(v.to_string());
        }
        if let Some(v) = m.get(schema::Key::InternetMessageId) {
            r.message_id = Some(v.to_string());
        }
        // The subject is the exception: the map is the only path that
        // requires the Topic/NormalizedSubject duplicate, and the
        // shape-based fallback supplies attachment paths instead of
        // subjects, so an empty map result must win.
        r.subject = m.get(schema::Key::NormalizedSubject).map(str::to_string);
        r.recipients = m
            .all(schema::Key::Recipient)
            .iter()
            .filter(|t| Some(**t) != r.sender.as_deref())
            .map(|t| t.to_string())
            .collect();
        r.preview = m.get(schema::Key::Preview).map(str::to_string);
        let sidecars = if r.html.is_some() {
            r.body_source = Some("inline");
            Vec::new()
        } else {
            body::sidecar_names(loc.span)
        };
        let convo_ids = m.all(schema::Key::ConversationId).iter().map(|s| s.to_string()).collect();
        records.push(RecordFacts { anchor: loc.anchor, record: r, convo_ids, sidecars });
    }
    BlockFacts {
        folders: folder::observations(data),
        candidates: folder::candidates(data, needle),
        keys: folder::keys(data, needle),
        events: calendar::observations(data),
        attachments: attachment::observations(data),
        records,
    }
}

/// Facts from the previous run, by block offset and header bytes.
pub struct Cache {
    bytes: Vec<u8>,
    entries: HashMap<(usize, [u8; KEY]), std::ops::Range<usize>>,
    /// Encoded facts for this run's verified blocks, written back by `save`.
    next: Vec<(usize, [u8; KEY], Vec<u8>)>,
    pub reused: usize,
    pub decoded: usize,
}

impl Cache {
    /// Read a cache file. A missing, foreign or damaged file is an empty cache.
    pub fn open(path: &Path) -> Cache {
        let bytes = fs::read(path).unwrap_or_default();
        let entries = index(&bytes).unwrap_or_default();
        Cache { bytes, entries, next: Vec::new(), reused: 0, decoded: 0 }
    }

    /// This block's facts: cached when the same header sat at the same
    /// offset last time, otherwise decompressed and extracted now.
    pub fn facts(
        &mut self,
        mm: &[u8],
        offset: usize,
        verified: &block::Verified,
        needle: &[u8],
    ) -> Result<BlockFacts, block::ParseError> {
        let key: [u8; KEY] = mm[offset..offset + KEY].try_into().unwrap();
        if let Some(found) = self.entries.get(&(offset, key)).and_then(|r| {
            let encoded = &self.bytes[r.clone()];
            decode(encoded).map(|f| (f, encoded.to_vec()))
        }) {
            self.reused += 1;
            self.next.push((offset, key, found.1));
            return Ok(found.0);
        }
        let b = block::inflate(mm, verified)?;
        let found = extract(offset, &b.data, needle);
        self.decoded += 1;
        self.next.push((offset, key, encode(&found)));
        Ok(found)
    }

    /// Replace the cache file with this run's facts, owner-only. A run that
    /// reused every entry and decoded nothing leaves the file as it is.
    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        if self.decoded == 0 && self.reused == self.entries.len() {
            return Ok(());
        }
        let mut out = Vec::new();
        out.extend(MAGIC);
        put_str(&mut out, SOURCE);
        put_u64(&mut out, self.next.len() as u64);
        for (offset, key, encoded) in &self.next {
            put_u64(&mut out, *offset as u64);
            out.extend(key);
            put_u64(&mut out, encoded.len() as u64);
            out.extend(block::crc32(encoded).to_le_bytes());
            out.extend(encoded);
        }
        let mut name = path.as_os_str().to_owned();
        name.push(".tmp");
        let temporary = Path::new(&name);
        let _ = fs::remove_file(temporary);
        let mut file =
            fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(temporary)?;
        file.write_all(&out)?;
        file.sync_all()?;
        fs::rename(temporary, path)
    }
}

fn index(bytes: &[u8]) -> Option<HashMap<(usize, [u8; KEY]), std::ops::Range<usize>>> {
    let mut r = Reader { b: bytes, p: 0 };
    if r.take(MAGIC.len())? != MAGIC || r.str()? != SOURCE {
        return None;
    }
    let mut entries = HashMap::new();
    for _ in 0..r.u64()? {
        let offset = r.usize()?;
        let key: [u8; KEY] = r.take(KEY)?.try_into().ok()?;
        let len = r.usize()?;
        let crc = u32::from_le_bytes(r.take(4)?.try_into().ok()?);
        let start = r.p;
        if block::crc32(r.take(len)?) == crc {
            entries.insert((offset, key), start..start + len);
        }
    }
    Some(entries)
}

// ---- encoding ---------------------------------------------------------------
//
// Every struct is destructured without `..`, so a field added to a decoded
// type fails to compile here until the cache carries it too.

fn put_u64(out: &mut Vec<u8>, v: u64) {
    out.extend(v.to_le_bytes());
}

fn put_usize(out: &mut Vec<u8>, v: usize) {
    put_u64(out, v as u64);
}

fn put_i64(out: &mut Vec<u8>, v: i64) {
    out.extend(v.to_le_bytes());
}

fn put_bool(out: &mut Vec<u8>, v: bool) {
    out.push(v as u8);
}

fn put_bytes(out: &mut Vec<u8>, v: &[u8]) {
    put_usize(out, v.len());
    out.extend(v);
}

fn put_str(out: &mut Vec<u8>, v: &str) {
    put_bytes(out, v.as_bytes());
}

fn put_opt<T>(out: &mut Vec<u8>, v: Option<T>, put: impl FnOnce(&mut Vec<u8>, T)) {
    put_bool(out, v.is_some());
    if let Some(v) = v {
        put(out, v);
    }
}

fn put_list<T>(out: &mut Vec<u8>, items: impl ExactSizeIterator<Item = T>, mut put: impl FnMut(&mut Vec<u8>, T)) {
    put_usize(out, items.len());
    for item in items {
        put(out, item);
    }
}

fn put_opt_str(out: &mut Vec<u8>, v: &Option<String>) {
    put_opt(out, v.as_deref(), put_str);
}

fn put_strings(out: &mut Vec<u8>, v: &[String]) {
    put_list(out, v.iter(), |o, s| put_str(o, s));
}

fn put_parent(out: &mut Vec<u8>, p: folder::Parent) {
    match p {
        folder::Parent::Top => out.push(0),
        folder::Parent::Folder(id) => {
            out.push(1);
            put_u64(out, id);
        }
        folder::Parent::Unknown => out.push(2),
    }
}

fn put_record(out: &mut Vec<u8>, r: &record::Record) {
    let record::Record {
        offset,
        sender,
        sender_name,
        recipients,
        folder_ids,
        local_keys,
        subject,
        message_id,
        body,
        html,
        sent_unix,
        subject_inherited,
        preview,
        body_source,
        copies,
    } = r;
    put_usize(out, *offset);
    put_opt_str(out, sender);
    put_opt_str(out, sender_name);
    put_strings(out, recipients);
    put_list(out, folder_ids.iter(), |o, v| put_u64(o, *v));
    put_list(out, local_keys.iter(), |o, v| put_u64(o, *v));
    put_opt_str(out, subject);
    put_opt_str(out, message_id);
    put_str(out, body);
    put_opt_str(out, html);
    put_opt(out, *sent_unix, put_i64);
    put_bool(out, *subject_inherited);
    put_opt_str(out, preview);
    out.push(match body_source {
        None => 0,
        Some("inline") => 1,
        Some("efm") => 2,
        Some(_) => 3,
    });
    put_usize(out, *copies);
}

fn put_event(out: &mut Vec<u8>, e: &calendar::Event) {
    let calendar::Event {
        goid,
        start_unix,
        end_unix,
        subject,
        location,
        organizer_name,
        organizer_email,
        body,
        attendees,
    } = e;
    put_str(out, goid);
    put_i64(out, *start_unix);
    put_i64(out, *end_unix);
    put_opt_str(out, subject);
    put_opt_str(out, location);
    put_opt_str(out, organizer_name);
    put_opt_str(out, organizer_email);
    put_opt_str(out, body);
    put_list(out, attendees.iter(), |o, a| {
        let calendar::Attendee { name, email } = a;
        put_str(o, name);
        put_str(o, email);
    });
}

fn put_attachment(out: &mut Vec<u8>, a: &attachment::Attachment) {
    let attachment::Attachment { id, message_key, name, name_verified, content_type, content_id, size, path } = a;
    put_u64(out, *id);
    put_u64(out, *message_key);
    put_str(out, name);
    put_bool(out, *name_verified);
    put_opt_str(out, content_type);
    put_opt_str(out, content_id);
    put_u64(out, *size);
    put_opt_str(out, path);
}

pub fn encode(f: &BlockFacts) -> Vec<u8> {
    let BlockFacts { folders, candidates, keys, events, attachments, records } = f;
    let mut out = Vec::new();
    put_list(&mut out, folders.iter(), |o, obs| match obs {
        folder::Observation::Folder { id, account_id, name, parent, owners } => {
            o.push(0);
            put_u64(o, *id);
            put_u64(o, *account_id);
            put_str(o, name);
            put_parent(o, *parent);
            put_list(o, owners.iter(), |o, v| put_u64(o, *v));
        }
        folder::Observation::Member { key, folder } => {
            o.push(1);
            put_u64(o, *key);
            put_u64(o, *folder);
        }
    });
    let folder::Candidates { views, messages, expanded } = candidates;
    for pairs in [views, messages] {
        put_list(&mut out, pairs.iter(), |o, (a, v)| {
            put_usize(o, *a);
            put_u64(o, *v);
        });
    }
    put_list(&mut out, expanded.iter(), |o, (a, id, account)| {
        put_usize(o, *a);
        put_u64(o, *id);
        put_u64(o, *account);
    });
    put_list(&mut out, keys.iter(), |o, (a, k)| {
        put_usize(o, *a);
        put_u64(o, *k);
    });
    put_list(&mut out, events.iter(), |o, e| {
        let calendar::Observation { goid, start, event } = e;
        put_str(o, goid);
        put_i64(o, *start);
        put_opt(o, event.as_ref(), |o, (bytes, ev)| {
            put_bytes(o, bytes);
            put_event(o, ev);
        });
    });
    put_list(&mut out, attachments.iter(), |o, a| put_opt(o, a.as_ref(), put_attachment));
    put_list(&mut out, records.iter(), |o, r| {
        let RecordFacts { anchor, record, convo_ids, sidecars } = r;
        put_usize(o, *anchor);
        put_record(o, record);
        put_strings(o, convo_ids);
        put_strings(o, sidecars);
    });
    out
}

struct Reader<'a> {
    b: &'a [u8],
    p: usize,
}

impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Option<&'a [u8]> {
        let s = self.b.get(self.p..self.p.checked_add(n)?)?;
        self.p += n;
        Some(s)
    }
    fn u8(&mut self) -> Option<u8> {
        Some(self.take(1)?[0])
    }
    fn u64(&mut self) -> Option<u64> {
        Some(u64::from_le_bytes(self.take(8)?.try_into().ok()?))
    }
    fn usize(&mut self) -> Option<usize> {
        usize::try_from(self.u64()?).ok()
    }
    fn i64(&mut self) -> Option<i64> {
        Some(i64::from_le_bytes(self.take(8)?.try_into().ok()?))
    }
    fn bool(&mut self) -> Option<bool> {
        match self.u8()? {
            0 => Some(false),
            1 => Some(true),
            _ => None,
        }
    }
    fn bytes(&mut self) -> Option<Vec<u8>> {
        let n = self.usize()?;
        Some(self.take(n)?.to_vec())
    }
    fn str(&mut self) -> Option<String> {
        String::from_utf8(self.bytes()?).ok()
    }
    fn opt<T>(&mut self, read: impl FnOnce(&mut Self) -> Option<T>) -> Option<Option<T>> {
        Some(if self.bool()? { Some(read(self)?) } else { None })
    }
    fn opt_str(&mut self) -> Option<Option<String>> {
        self.opt(Self::str)
    }
    /// A length-prefixed list; the length is bounded by the bytes left, so a
    /// damaged count cannot allocate more than the file could hold.
    fn list<T>(&mut self, mut read: impl FnMut(&mut Self) -> Option<T>) -> Option<Vec<T>> {
        let n = self.usize()?;
        if n > self.b.len() - self.p {
            return None;
        }
        (0..n).map(|_| read(self)).collect()
    }
    fn strings(&mut self) -> Option<Vec<String>> {
        self.list(Self::str)
    }
    fn parent(&mut self) -> Option<folder::Parent> {
        Some(match self.u8()? {
            0 => folder::Parent::Top,
            1 => folder::Parent::Folder(self.u64()?),
            2 => folder::Parent::Unknown,
            _ => return None,
        })
    }
    fn record(&mut self) -> Option<record::Record> {
        Some(record::Record {
            offset: self.usize()?,
            sender: self.opt_str()?,
            sender_name: self.opt_str()?,
            recipients: self.strings()?,
            folder_ids: self.list(Self::u64)?.into_iter().collect::<BTreeSet<_>>(),
            local_keys: self.list(Self::u64)?.into_iter().collect::<BTreeSet<_>>(),
            subject: self.opt_str()?,
            message_id: self.opt_str()?,
            body: self.str()?,
            html: self.opt_str()?,
            sent_unix: self.opt(Self::i64)?,
            subject_inherited: self.bool()?,
            preview: self.opt_str()?,
            body_source: match self.u8()? {
                0 => None,
                1 => Some("inline"),
                2 => Some("efm"),
                _ => return None,
            },
            copies: self.usize()?,
        })
    }
    fn event(&mut self) -> Option<calendar::Event> {
        Some(calendar::Event {
            goid: self.str()?,
            start_unix: self.i64()?,
            end_unix: self.i64()?,
            subject: self.opt_str()?,
            location: self.opt_str()?,
            organizer_name: self.opt_str()?,
            organizer_email: self.opt_str()?,
            body: self.opt_str()?,
            attendees: self.list(|r| Some(calendar::Attendee { name: r.str()?, email: r.str()? }))?,
        })
    }
    fn attachment(&mut self) -> Option<attachment::Attachment> {
        Some(attachment::Attachment {
            id: self.u64()?,
            message_key: self.u64()?,
            name: self.str()?,
            name_verified: self.bool()?,
            content_type: self.opt_str()?,
            content_id: self.opt_str()?,
            size: self.u64()?,
            path: self.opt_str()?,
        })
    }
}

pub fn decode(bytes: &[u8]) -> Option<BlockFacts> {
    let mut r = Reader { b: bytes, p: 0 };
    let folders = r.list(|r| {
        Some(match r.u8()? {
            0 => folder::Observation::Folder {
                id: r.u64()?,
                account_id: r.u64()?,
                name: r.str()?,
                parent: r.parent()?,
                owners: r.list(Reader::u64)?,
            },
            1 => folder::Observation::Member { key: r.u64()?, folder: r.u64()? },
            _ => return None,
        })
    })?;
    let views = r.list(|r| Some((r.usize()?, r.u64()?)))?;
    let messages = r.list(|r| Some((r.usize()?, r.u64()?)))?;
    let expanded = r.list(|r| Some((r.usize()?, r.u64()?, r.u64()?)))?;
    let keys = r.list(|r| Some((r.usize()?, r.u64()?)))?.into_iter().collect();
    let events = r.list(|r| {
        Some(calendar::Observation {
            goid: r.str()?,
            start: r.i64()?,
            event: r.opt(|r| Some((r.bytes()?, r.event()?)))?,
        })
    })?;
    let attachments = r.list(|r| r.opt(Reader::attachment))?;
    let records = r.list(|r| {
        Some(RecordFacts { anchor: r.usize()?, record: r.record()?, convo_ids: r.strings()?, sidecars: r.strings()? })
    })?;
    (r.p == bytes.len()).then_some(BlockFacts {
        folders,
        candidates: folder::Candidates { views, messages, expanded },
        keys,
        events,
        attachments,
        records,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> BlockFacts {
        let record = record::Record {
            offset: 4096,
            sender: Some("pat@example.test".into()),
            sender_name: None,
            recipients: vec!["lee@example.test".into(), "kim@example.test".into()],
            folder_ids: BTreeSet::from([5, 6]),
            local_keys: BTreeSet::from([9]),
            subject: Some("Synthetic é 🚀".into()),
            message_id: Some("<1@example.test>".into()),
            body: "body text".into(),
            html: Some("<p>body text</p>".into()),
            sent_unix: Some(-7),
            subject_inherited: true,
            preview: None,
            body_source: Some("efm"),
            copies: 3,
        };
        let event = calendar::Event {
            goid: "040000".into(),
            start_unix: 1,
            end_unix: 2,
            subject: Some("Review".into()),
            location: None,
            organizer_name: Some("Ada".into()),
            organizer_email: Some("ada@example.test".into()),
            body: Some("Notes".into()),
            attendees: vec![calendar::Attendee { name: "Bo".into(), email: "bo@example.test".into() }],
        };
        let attachment = attachment::Attachment {
            id: 80,
            message_key: 70,
            name: "Lease.pdf".into(),
            name_verified: true,
            content_type: Some("application/pdf".into()),
            content_id: None,
            size: 4096,
            path: Some("~/Files/S0/1/Attachments/2/Lease[1].pdf".into()),
        };
        BlockFacts {
            folders: vec![
                folder::Observation::Folder {
                    id: 50,
                    account_id: 7,
                    name: "Deal A".into(),
                    parent: folder::Parent::Folder(49),
                    owners: vec![60, 61],
                },
                folder::Observation::Folder {
                    id: 51,
                    account_id: 7,
                    name: "Top".into(),
                    parent: folder::Parent::Unknown,
                    owners: vec![],
                },
                folder::Observation::Member { key: 70, folder: 50 },
            ],
            candidates: folder::Candidates {
                views: vec![(110, 60)],
                messages: vec![(220, 70)],
                expanded: vec![(500, 50, 7)],
            },
            keys: BTreeMap::from([(110, 70), (500, 71)]),
            events: vec![
                calendar::Observation { goid: "040000".into(), start: 1, event: Some((vec![1, 2, 3], event)) },
                calendar::Observation { goid: "040001".into(), start: 5, event: None },
            ],
            attachments: vec![Some(attachment), None],
            records: vec![RecordFacts {
                anchor: 300,
                record,
                convo_ids: vec!["c1".into()],
                sidecars: vec!["1a.dat".into()],
            }],
        }
    }

    fn debug(f: &BlockFacts) -> String {
        let records: Vec<_> = f
            .records
            .iter()
            .map(|r| format!("{} {:?} {:?} {:?}", r.anchor, r.record, r.convo_ids, r.sidecars))
            .collect();
        format!(
            "{:?} {:?} {:?} {:?} {:?} {:?}",
            f.folders, f.candidates, f.keys, f.events, f.attachments, records
        )
    }

    #[test]
    fn every_fact_survives_the_cache_encoding() {
        let facts = sample();
        let encoded = encode(&facts);
        assert_eq!(debug(&decode(&encoded).unwrap()), debug(&facts));
        assert_eq!(encode(&decode(&encoded).unwrap()), encoded);
    }

    #[test]
    fn truncated_or_extended_encodings_are_rejected() {
        let encoded = encode(&sample());
        for cut in 0..encoded.len() {
            assert!(decode(&encoded[..cut]).is_none(), "cut at {cut}");
        }
        let mut longer = encoded.clone();
        longer.push(0);
        assert!(decode(&longer).is_none());
    }

    #[test]
    fn a_foreign_or_damaged_cache_file_has_no_entries() {
        assert!(index(b"").is_none());
        assert!(index(b"HXPFACT1").is_none());
        let mut file = MAGIC.to_vec();
        put_str(&mut file, "another build");
        put_u64(&mut file, 0);
        assert!(index(&file).is_none());
    }
}
