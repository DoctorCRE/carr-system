//! Calendar events, decoded from serialized class-`0x006b` objects.
//!
//! The store holds no `IPM.Appointment` anchor at all. A calendar item is a
//! serialized object (envelope tag `0x0455`, class `0x006b`) with this layout:
//!
//! ```text
//! +574  u64  start, .NET ticks (UTC)
//! +582  u64  end,   .NET ticks (UTC)
//!  ...  UTF-16LE Global Object ID, "040000008200E00074C5B7101A82E008..."
//!  ...  time-zone tables: strings with a one-byte length prefix
//!  ...  NUL-terminated UTF-16LE fields: subject, location, organizer name,
//!       organizer address, the owner's name, notes, and the subject again
//!  end  attendee block: u32 count, then per attendee a length-prefixed
//!       name, a length-prefixed address and 12 status bytes, to the end
//! ```
//!
//! The same class also serializes working-hours blocks. Those carry no Global
//! Object ID, which is what separates them from meetings.
//!
//! The order of the NUL-terminated fields varies between revisions of one
//! item, so they are found by what they are rather than where they sit: the
//! subject is the field written twice, and subject, location, organizer name
//! and organizer address sit together in that order.
//!
//! Global Object ID plus start groups observed schedule candidates. It cannot
//! distinguish a moved occurrence from a recurring exception. Cancellation,
//! deletion, revision ordering, recurrence and response semantics are unverified.
//! No candidate establishes current schedule state or attendance.

use std::collections::{BTreeMap, BTreeSet};

const TAG: u16 = 0x0455;
const CLASS: u16 = 0x006b;
const START: usize = 574;
const END: usize = 582;
const MIN_OBJECT: usize = 600;
const MAX_ATTENDEES: u32 = 500;
const STATUS_BYTES: usize = 12;
const GOID_PREFIX: &str = "040000008200E00074C5B7101A82E008";
const UNIX_EPOCH_TICKS: u64 = 621_355_968_000_000_000;
// 2015-01-01 .. 2040-01-01: events are scheduled ahead, so the window reaches
// further forward than the mail send-time window in record.rs.
const TICK_LO: u64 = 635_556_672_000_000_000;
const TICK_HI: u64 = 643_446_720_000_000_000;

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Attendee {
    pub name: String,
    pub email: String,
}

#[derive(Clone, Debug, Default)]
pub struct Event {
    pub goid: String,
    pub start_unix: i64,
    pub end_unix: i64,
    pub subject: Option<String>,
    pub location: Option<String>,
    pub organizer_name: Option<String>,
    pub organizer_email: Option<String>,
    pub body: Option<String>,
    pub attendees: Vec<Attendee>,
}

/// Distinct serialized candidates, never combined into an invented revision.
#[derive(Default)]
pub struct Index {
    pub events: BTreeMap<Vec<u8>, Event>,
    /// Identities seen on any revision, whether or not one fully decoded.
    pub identities: BTreeSet<(String, i64)>,
    pub objects: usize,
}

/// One dated calendar object; `event` holds its bytes and decode when it parses.
#[derive(Debug)]
pub struct Observation {
    pub goid: String,
    pub start: i64,
    pub event: Option<(Vec<u8>, Event)>,
}

pub fn observations(data: &[u8]) -> Vec<Observation> {
    objects(data)
        .into_iter()
        .filter_map(|obj| {
            let (Some(goid), Some(start), Some(end)) = (goid(obj), tick(obj, START), tick(obj, END)) else {
                return None;
            };
            let event = (end >= start)
                .then(|| parse(obj, goid.clone(), start, end))
                .flatten()
                .map(|ev| (obj.to_vec(), ev));
            Some(Observation { goid, start, event })
        })
        .collect()
}

impl Index {
    #[cfg(test)]
    pub fn observe(&mut self, data: &[u8]) {
        for observation in observations(data) {
            self.apply(observation);
        }
    }

    pub fn apply(&mut self, Observation { goid, start, event }: Observation) {
        self.objects += 1;
        self.identities.insert((goid, start));
        if let Some((bytes, ev)) = event {
            self.events.entry(bytes).or_insert(ev);
        }
    }
}

/// Bodies of class-`0x006b` objects inside one decompressed block payload.
fn objects(data: &[u8]) -> Vec<&[u8]> {
    let mut head = [0u8; 4];
    head[..2].copy_from_slice(&5u16.to_le_bytes());
    head[2..].copy_from_slice(&TAG.to_le_bytes());
    memchr::memmem::find_iter(data, &head)
        .filter_map(|q| {
            let len = u32::from_le_bytes(data.get(q + 4..q + 8)?.try_into().ok()?) as usize;
            let class = u16::from_le_bytes(data.get(q + 10..q + 12)?.try_into().ok()?);
            (data.get(q + 8..q + 10)? == [0, 0] && class == CLASS && len >= MIN_OBJECT)
                .then(|| data.get(q + 10..q + len))
                .flatten()
        })
        .collect()
}

fn tick(obj: &[u8], at: usize) -> Option<i64> {
    let v = u64::from_le_bytes(obj.get(at..at + 8)?.try_into().ok()?);
    (TICK_LO..TICK_HI)
        .contains(&v)
        .then(|| ((v - UNIX_EPOCH_TICKS) / 10_000_000) as i64)
}

fn utf16(bytes: &[u8]) -> Vec<u16> {
    bytes.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect()
}

fn goid(obj: &[u8]) -> Option<String> {
    let needle: Vec<u8> = GOID_PREFIX.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let at = memchr::memmem::find(obj, &needle)?;
    let id: String = utf16(&obj[at..])
        .into_iter()
        .take_while(|u| (*u as u8 as u16) == *u && (*u as u8).is_ascii_hexdigit())
        .map(|u| u as u8 as char)
        .collect();
    Some(id)
}

/// The attendee block starting at `p`, if it parses exactly to the object end.
fn attendees_at(obj: &[u8], p: usize) -> Option<Vec<Attendee>> {
    let n = u32::from_le_bytes(obj.get(p..p + 4)?.try_into().ok()?);
    if n > MAX_ATTENDEES {
        return None;
    }
    let mut q = p + 4;
    let mut out = Vec::new();
    for _ in 0..n {
        let mut pair = [String::new(), String::new()];
        for slot in &mut pair {
            let len = *obj.get(q)? as usize;
            if len % 2 == 1 {
                return None;
            }
            *slot = String::from_utf16(&utf16(obj.get(q + 1..q + 1 + len)?)).ok()?;
            q += 1 + len;
        }
        q += STATUS_BYTES;
        let [name, email] = pair;
        out.push(Attendee { name, email });
    }
    (q == obj.len()).then_some(out)
}

fn is_email(s: &str) -> bool {
    let Some((local, domain)) = s.split_once('@') else { return false };
    let tld = domain.rsplit('.').next().unwrap_or("");
    !local.is_empty()
        && !s.chars().any(char::is_whitespace)
        && !domain.contains('@')
        && domain.contains('.')
        && tld.len() >= 2
        && tld.chars().all(|c| c.is_ascii_alphabetic())
}

/// Text rather than binary decoded as UTF-16: the store's text is Latin, so a
/// run with a fifth or more of its characters outside Latin is noise.
fn is_text(s: &str) -> bool {
    let total = s.chars().count();
    let latin = s.chars().filter(|c| (*c as u32) < 0x0250 || matches!(*c, '\u{2000}'..='\u{206f}')).count();
    total == 0 || latin * 5 >= total * 4
}

/// True when the object's last field is a NUL-terminated copy of an earlier text field.
fn ends_at_repeated_subject(obj: &[u8], g: usize) -> bool {
    let len = obj.len();
    if len < g + 6 || obj[len - 2..] != [0, 0] {
        return false;
    }
    let lo = g + (len - g) % 2;
    let fields: Vec<String> = utf16(&obj[lo..len - 2]).split(|u| *u == 0).map(String::from_utf16_lossy).collect();
    let Some((last, earlier)) = fields.split_last() else { return false };
    last.chars().count() >= 2 && is_text(last) && earlier.iter().any(|f| f.ends_with(last.as_str()))
}

fn parse(obj: &[u8], goid: String, start: i64, end: i64) -> Option<Event> {
    let g = memchr::memmem::find(obj, &GOID_PREFIX.encode_utf16().flat_map(u16::to_le_bytes).collect::<Vec<u8>>())?;
    // The earliest block that parses to the end. Scanning from the end instead
    // stops at the trailing status bytes, which read as a count of zero.
    // An item with no attendees (an own appointment, a block of time) carries
    // no attendee block: its NUL-terminated fields run to the object end, which
    // is then the subject written a second time. Anything else at the end is an
    // attendee list this parser could not read, so the item stays undecoded and
    // counted rather than becoming a meeting with nobody in it. A zero-count
    // block is held to the same test, since stray zero bytes read as one.
    let (ap, attendees) = (g + 2..obj.len().saturating_sub(3))
        .filter(|p| obj[p - 2..*p] == [0, 0])
        .find_map(|p| {
            attendees_at(obj, p)
                .filter(|a| !a.is_empty() || ends_at_repeated_subject(&obj[..p], g))
                .map(|a| (p, a))
        })
        .or_else(|| ends_at_repeated_subject(obj, g).then(|| (obj.len(), Vec::new())))?;
    // The length-prefixed time-zone strings shift byte parity, so the field
    // run is aligned to where it ends, not to where the object starts.
    let lo = g + (ap - g) % 2;
    // Raw runs, not text-filtered: the run holding the first copy of the
    // subject also holds the tail of the time-zone tables.
    let raw: Vec<String> = utf16(&obj[lo..ap]).split(|u| *u == 0).map(String::from_utf16_lossy).collect();
    let parts: Vec<String> =
        raw.iter().map(|s| if is_text(s) { s.clone() } else { String::new() }).collect();
    let width = |s: &str| s.chars().count();

    let mut subject: Option<(usize, &str)> = None;
    for (i, x) in parts.iter().enumerate() {
        if width(x) < 2 || is_email(x) {
            continue;
        }
        for (j, y) in raw.iter().enumerate() {
            let longer = subject.map_or(true, |(_, s)| width(x) > width(s));
            if j != i && y.ends_with(x.as_str()) && width(y) > width(x) + 2 && longer {
                subject = Some((j, x));
            } else if j > i && y == x && longer {
                subject = Some((i, x));
            }
        }
    }
    let (subject_at, subject) = subject?;

    let (mut organizer_name, mut organizer_email, mut location) = (None, None, None);
    if let Some(i) = parts.iter().position(|x| is_email(x)) {
        organizer_email = Some(parts[i].clone());
        organizer_name = i.checked_sub(1).map(|k| parts[k].clone()).filter(|s| !s.is_empty());
        if i >= 3 && subject_at == i - 3 {
            location = Some(parts[i - 2].clone()).filter(|s| !s.is_empty());
        }
    }
    let body = parts
        .iter()
        .filter(|x| x.as_str() != subject && (x.contains("\r\n") || width(x) > 60))
        .max_by_key(|x| width(x))
        .cloned();

    Some(Event {
        goid,
        start_unix: start,
        end_unix: end,
        subject: Some(subject.to_string()),
        location,
        organizer_name,
        organizer_email,
        body,
        attendees,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn u16le(s: &str) -> Vec<u8> {
        s.encode_utf16().flat_map(u16::to_le_bytes).collect()
    }

    fn lp(s: &str) -> Vec<u8> {
        let b = u16le(s);
        let mut out = vec![b.len() as u8];
        out.extend(b);
        out
    }

    fn ticks(unix: i64) -> [u8; 8] {
        (unix as u64 * 10_000_000 + UNIX_EPOCH_TICKS).to_le_bytes()
    }

    /// A synthetic class-0x006b object in the observed layout.
    fn object(with_goid: bool, attendees: &[(&str, &str)]) -> Vec<u8> {
        let mut obj = vec![0u8; START];
        obj[..2].copy_from_slice(&CLASS.to_le_bytes());
        obj.extend(ticks(1_793_000_000));
        obj.extend(ticks(1_793_003_600));
        obj.extend(vec![0u8; 517]);
        if with_goid {
            obj.extend(u16le(&format!("{GOID_PREFIX}00000000AAAA")));
            obj.extend([0, 0]);
        }
        obj.extend(lp("America/Chicago"));
        obj.extend(lp("US/Central"));
        for field in ["Quarterly Site Review", "Example Room", "Ada Example", "ada@example.test",
                      "Owner Example", "Agenda line one\r\nline two", "Quarterly Site Review"] {
            obj.extend(u16le(field));
            obj.extend([0, 0]);
        }
        obj.extend((attendees.len() as u32).to_le_bytes());
        for (name, email) in attendees {
            obj.extend(lp(name));
            obj.extend(lp(email));
            obj.extend([0u8; STATUS_BYTES]);
        }
        obj
    }

    fn envelope(obj: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        out.extend(5u16.to_le_bytes());
        out.extend(TAG.to_le_bytes());
        out.extend(((obj.len() + 10) as u32).to_le_bytes());
        out.extend([0, 0]);
        out.extend(obj);
        out
    }

    #[test]
    fn decodes_a_meeting() {
        let mut idx = Index::default();
        idx.observe(&envelope(&object(true, &[("Bo Example", "bo@example.test")])));
        assert_eq!(idx.events.len(), 1, "one meeting decoded");
        let ev = idx.events.values().next().unwrap();
        assert_eq!(ev.start_unix, 1_793_000_000, "start read at +574");
        assert_eq!(ev.end_unix, 1_793_003_600, "end read at +582");
        assert_eq!(ev.subject.as_deref(), Some("Quarterly Site Review"), "subject is the field written twice");
        assert_eq!(ev.location.as_deref(), Some("Example Room"), "location follows the subject");
        assert_eq!(ev.organizer_email.as_deref(), Some("ada@example.test"), "organizer address");
        assert_eq!(ev.organizer_name.as_deref(), Some("Ada Example"), "organizer name precedes the address");
        assert_eq!(ev.body.as_deref(), Some("Agenda line one\r\nline two"), "notes");
        assert_eq!(ev.attendees, vec![Attendee { name: "Bo Example".into(), email: "bo@example.test".into() }]);
    }

    #[test]
    fn a_meeting_with_no_attendees_still_decodes() {
        let mut idx = Index::default();
        idx.observe(&envelope(&object(true, &[])));
        assert_eq!(idx.events.len(), 1, "a zero-attendee block is still a block");
        assert!(idx.events.values().next().unwrap().attendees.is_empty());
    }

    #[test]
    fn an_appointment_without_an_attendee_block_decodes() {
        // Observed on a TEST-account appointment (2026-10-21 study pair): the
        // object ends at the repeated subject, with no attendee count at all.
        let mut obj = object(true, &[]);
        obj.truncate(obj.len() - 4);
        let mut idx = Index::default();
        idx.observe(&envelope(&obj));
        assert_eq!(idx.events.len(), 1, "the identity decodes instead of vanishing");
        let ev = idx.events.values().next().unwrap();
        assert_eq!(ev.subject.as_deref(), Some("Quarterly Site Review"));
        assert_eq!(ev.location.as_deref(), Some("Example Room"));
        assert_eq!(ev.organizer_email.as_deref(), Some("ada@example.test"));
        assert!(ev.attendees.is_empty());
    }

    #[test]
    fn an_unreadable_attendee_list_stays_undecoded() {
        // Joe as attendee plus two stray zero bytes: the attendee block no longer
        // parses to the end, and it must not turn into a meeting with no one in it.
        let mut obj = object(true, &[("Joe Example", "joe@example.test")]);
        obj.extend([0, 0]);
        let mut idx = Index::default();
        idx.observe(&envelope(&obj));
        assert_eq!(idx.objects, 1, "the identity is still counted");
        assert!(idx.events.is_empty(), "and left undecoded");
    }

    #[test]
    fn reversed_times_are_counted_but_not_exported_as_candidates() {
        let mut obj = object(true, &[]);
        obj[END..END + 8].copy_from_slice(&ticks(1_792_999_999));
        let mut idx = Index::default();
        idx.observe(&envelope(&obj));
        assert_eq!(idx.identities.len(), 1, "the unreadable item remains visible in counts");
        assert!(idx.events.is_empty(), "an event cannot end before it starts");
    }

    #[test]
    fn working_hours_without_a_global_id_are_not_meetings() {
        let mut idx = Index::default();
        idx.observe(&envelope(&object(false, &[])));
        assert!(idx.events.is_empty(), "no Global Object ID, no meeting");
        assert_eq!(idx.objects, 0, "and it is not counted as a meeting object");
    }

    #[test]
    fn distinct_revisions_remain_separate_candidates() {
        let mut idx = Index::default();
        let one = envelope(&object(true, &[]));
        let two = envelope(&object(true, &[("Bo Example", "bo@example.test")]));
        idx.observe(&[one, two].concat());
        assert_eq!(idx.objects, 2, "both revisions seen");
        assert_eq!(idx.events.len(), 2, "an attendee removal must not be merged with an older list");
        let sizes: BTreeSet<_> = idx.events.values().map(|e| e.attendees.len()).collect();
        assert_eq!(sizes, BTreeSet::from([0, 1]));
    }

    #[test]
    fn opaque_lifecycle_and_response_changes_remain_separate_candidates() {
        let original = object(true, &[("Bo Example", "bo@example.test")]);
        let mut changed_metadata = original.clone();
        changed_metadata[100] = 1;
        let mut changed_response = original.clone();
        let at = changed_response.len() - STATUS_BYTES;
        changed_response[at] = 1;
        let mut idx = Index::default();
        for candidate in [&original, &changed_metadata, &changed_response, &original] {
            idx.observe(&envelope(candidate));
        }
        assert_eq!(idx.objects, 4);
        assert_eq!(idx.events.len(), 3, "unknown bytes cannot establish equivalent current state");
    }

}
