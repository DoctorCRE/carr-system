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
//!  ...  attendee block: u32 count, then per attendee a length-prefixed
//!       name, a length-prefixed address and 12 status bytes
//! ```
//!
//! Some objects store other heap data after the roster. If the end-based
//! parse fails, the decoder reads the exact roster span from slot +674.
//! It requires an organizer address at +882 and separate subject spans at
//! +866 and +1014 whose text matches after removing any cancellation prefix.
//!
//! The 12 status bytes are three little-endian u32 words: attendee kind,
//! response, and a word always seen as zero. A TEST-account invitation
//! (2026-10-28) pins kind 0 = required, 1 = optional and response 4 = no
//! response yet. Other response codes (0, 1 and 2 are seen) have no ground
//! truth: they are exported raw and unlabelled. An attendee's own copy of a
//! meeting does not track the other attendees' replies.
//!
//! An occurrence of a recurring series also carries its start at +2.
//! Outlook writes "Canceled: " before the subject of a meeting its organizer
//! cancelled; that stored copy is exported as `canceled`, the only lifecycle
//! the bytes carry. Every other candidate stays unsupported.
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

use crate::object::{self, Object};

const TAG: u16 = 0x0455;
const CLASS: u16 = 0x006b;
const START: usize = 574;
const OCCURRENCE: usize = 2;
const CANCELED: [&str; 2] = ["Canceled: ", "Cancelled: "];
const END: usize = 582;
const MIN_OBJECT: usize = 600;
const MAX_ATTENDEES: u32 = 500;
const STATUS_BYTES: usize = 12;
const GOID_PREFIX: &str = "040000008200E00074C5B7101A82E008";
const UNIX_EPOCH_TICKS: u64 = 621_355_968_000_000_000;
// 2015-01-01 .. 2040-01-01: events are scheduled ahead, so the window reaches
// further forward than any mail send time.
const TICK_LO: u64 = 635_556_672_000_000_000;
const TICK_HI: u64 = 643_446_720_000_000_000;

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Attendee {
    pub name: String,
    pub email: String,
    pub kind_code: u32,
    pub response_code: u32,
}

impl Attendee {
    /// Only codes with TEST-account ground truth get a label.
    pub fn kind(&self) -> Option<&'static str> {
        match self.kind_code {
            0 => Some("required"),
            1 => Some("optional"),
            _ => None,
        }
    }

    pub fn response(&self) -> Option<&'static str> {
        (self.response_code == 4).then_some("no_response")
    }
}

#[derive(Clone, Debug, Default)]
#[cfg_attr(test, derive(PartialEq))]
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
    pub canceled: bool,
    pub occurrence: bool,
}

/// Distinct serialized candidates, never combined into an invented revision.
#[derive(Default)]
pub struct Index {
    pub events: BTreeMap<Vec<u8>, Event>,
    /// Identities seen on any revision, whether or not one fully decoded.
    pub identities: BTreeSet<(String, i64)>,
    pub objects: usize,
}

impl Index {
    pub fn observe(&mut self, data: &[u8]) {
        for obj in objects(data) {
            let (Some(goid), Some(start), Some(end)) = (goid(obj), tick(obj, START), tick(obj, END)) else {
                continue;
            };
            self.objects += 1;
            self.identities.insert((goid.clone(), start));
            if end < start {
                continue;
            }
            let Some(ev) = parse(obj, &goid, start, end)
                .or_else(|| parse_roster_slot(obj, &goid, start, end))
            else {
                continue;
            };
            self.events.entry(obj.to_vec()).or_insert(ev);
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

/// The attendee block starting at `p`, if it consumes the supplied bytes.
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
        let status = obj.get(q..q + STATUS_BYTES)?;
        let word = |i: usize| u32::from_le_bytes([status[4 * i], status[4 * i + 1], status[4 * i + 2], status[4 * i + 3]]);
        let (kind_code, response_code) = (word(0), word(1));
        q += STATUS_BYTES;
        let [name, email] = pair;
        out.push(Attendee { name, email, kind_code, response_code });
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

fn parse(obj: &[u8], goid: &str, start: i64, end: i64) -> Option<Event> {
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

    // The first copy's run can start with the tail of the time-zone tables.
    let canceled = raw.iter().any(|r| CANCELED.iter().any(|p| r.ends_with(&format!("{p}{subject}"))));
    let occurrence = obj.get(OCCURRENCE..OCCURRENCE + 8) == obj.get(START..START + 8);
    Some(Event {
        goid: goid.to_owned(),
        start_unix: start,
        end_unix: end,
        subject: Some(subject.to_string()),
        location,
        organizer_name,
        organizer_email,
        body,
        attendees,
        canceled,
        occurrence,
    })
}

fn parse_roster_slot(obj: &[u8], goid: &str, start: i64, end: i64) -> Option<Event> {
    let record = Object::new(obj, TAG as usize - 10)?;
    let inline_slot = |at| {
        if record.external(at) {
            return None;
        }
        record.slot(at)?
    };
    let roster = inline_slot(674)?;
    let at = roster.as_ptr() as usize - obj.as_ptr() as usize;
    if obj.get(at.checked_sub(2)?..at)? != [0, 0] || at + roster.len() == obj.len() {
        return None;
    }
    let attendees = attendees_at(roster, 0)?;
    if attendees.is_empty() || attendees.iter().any(|attendee| !is_email(&attendee.email)) {
        return None;
    }

    let first = inline_slot(866)?;
    let second = inline_slot(1014)?;
    let overlaps = |left: &[u8], right: &[u8]| {
        let left = left.as_ptr_range();
        let right = right.as_ptr_range();
        left.start.max(right.start) < left.end.min(right.end)
    };
    if overlaps(first, second)
        || std::iter::once(roster)
            .chain([690, 826, 874, 882, 970].into_iter().filter_map(inline_slot))
            .any(|field| overlaps(first, field) || overlaps(second, field))
    {
        return None;
    }
    let first = object::text(first)?;
    let second = object::text(second)?;
    let (first, first_canceled) = split_cancellation(&first);
    let (subject, second_canceled) = split_cancellation(&second);
    if first != subject || subject.trim().is_empty() {
        return None;
    }

    let text = |at| inline_slot(at).and_then(object::text).filter(|s| !s.is_empty());
    let organizer_email = text(882).filter(|s| is_email(s))?;
    Some(Event {
        goid: goid.to_owned(),
        start_unix: start,
        end_unix: end,
        subject: Some(subject.to_owned()),
        location: text(826),
        organizer_name: text(874),
        organizer_email: Some(organizer_email),
        body: None,
        attendees,
        canceled: first_canceled || second_canceled,
        occurrence: obj.get(OCCURRENCE..OCCURRENCE + 8) == obj.get(START..START + 8),
    })
}

fn split_cancellation(subject: &str) -> (&str, bool) {
    // Python's agenda regex treats these four controls as whitespace too.
    let whitespace = |c: char| c.is_whitespace() || matches!(c, '\u{001c}'..='\u{001f}');
    if let Some((prefix, title)) = subject.split_once(':') {
        let prefix = prefix.trim_matches(whitespace);
        if CANCELED.iter().any(|p| prefix.eq_ignore_ascii_case(p.trim_end_matches(": "))) {
            return (title.trim_start_matches(whitespace), true);
        }
    }
    (subject, false)
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
        object_with(with_goid, "Quarterly Site Review", attendees)
    }

    fn object_with(with_goid: bool, first_subject: &str, attendees: &[(&str, &str)]) -> Vec<u8> {
        let status: Vec<_> = attendees.iter().map(|(n, e)| (*n, *e, [0u32; 3])).collect();
        object_status(with_goid, first_subject, &status)
    }

    fn object_status(with_goid: bool, first_subject: &str, attendees: &[(&str, &str, [u32; 3])]) -> Vec<u8> {
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
        for field in [first_subject, "Example Room", "Ada Example", "ada@example.test",
                      "Owner Example", "Agenda line one\r\nline two", "Quarterly Site Review"] {
            obj.extend(u16le(field));
            obj.extend([0, 0]);
        }
        obj.extend((attendees.len() as u32).to_le_bytes());
        for (name, email, status) in attendees {
            obj.extend(lp(name));
            obj.extend(lp(email));
            obj.extend(status.iter().flat_map(|w| w.to_le_bytes()));
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

    fn heap_field(obj: &mut Vec<u8>, at: usize, value: &[u8]) {
        let heap = TAG as usize - 10 + 7;
        let offset = (obj.len() - heap) as u32;
        obj[at..at + 4].copy_from_slice(&offset.to_le_bytes());
        obj[at + 4..at + 8].copy_from_slice(&(0x8000_0000 | value.len() as u32).to_le_bytes());
        obj.extend(value);
    }

    fn heap_meeting(first_subject: &str, second_subject: &str, organizer: &str) -> Vec<u8> {
        let mut obj = vec![0; TAG as usize - 10 + 7];
        obj[..2].copy_from_slice(&CLASS.to_le_bytes());
        obj[94..96].copy_from_slice(&7u16.to_le_bytes());
        obj[START..START + 8].copy_from_slice(&ticks(1_793_000_000));
        obj[END..END + 8].copy_from_slice(&ticks(1_793_003_600));
        obj.extend(u16le(&format!("{GOID_PREFIX}00000000AAAA\0")));
        for (at, value) in [(866, first_subject), (874, "Ada Example"), (882, organizer), (1014, second_subject)] {
            heap_field(&mut obj, at, &u16le(&format!("{value}\0")));
        }
        let roster = [1u32.to_le_bytes().to_vec(), lp("Bo Example"), lp("bo@example.test"), vec![0; 12]].concat();
        heap_field(&mut obj, 674, &roster);
        obj
    }

    fn main_event(obj: &[u8]) -> Option<Event> {
        parse(obj, &goid(obj)?, tick(obj, START)?, tick(obj, END)?)
    }

    #[test]
    fn decodes_a_roster_followed_by_other_heap_data() {
        let title = "Quarterly Site Review";
        for trailer in ["slot 970", "slot 970 and subject", "slot 690", "slot 826", "padding"] {
            let mut obj = heap_meeting(title, title, "ada@example.test");
            match trailer {
                "slot 970" => heap_field(&mut obj, 970, &[0xa5; 24]),
                "slot 970 and subject" => {
                    heap_field(&mut obj, 970, &[0xa5; 24]);
                    heap_field(&mut obj, 1014, &u16le(&format!("{title}\0")));
                }
                "slot 690" => heap_field(&mut obj, 690, &u16le("Meeting notes\0")),
                "slot 826" => heap_field(&mut obj, 826, &u16le("Example Room\0")),
                "padding" => obj.extend([0; 16]),
                _ => unreachable!(),
            }
            assert!(main_event(&obj).is_none(), "main misses {trailer}");
            let mut idx = Index::default();
            idx.observe(&envelope(&obj));
            let ev = idx.events.values().next().unwrap_or_else(|| panic!("missing meeting with {trailer}"));
            assert_eq!(ev.subject.as_deref(), Some(title), "{trailer}");
            assert_eq!(ev.organizer_email.as_deref(), Some("ada@example.test"));
            assert_eq!(ev.attendees, vec![Attendee { name: "Bo Example".into(), email: "bo@example.test".into(), ..Default::default() }]);
            assert!(!ev.canceled);
        }
    }

    #[test]
    fn marks_a_recovered_meeting_canceled_from_either_subject_slot() {
        let title = "Quarterly Site Review";
        let canceled = "Cancelled: Quarterly Site Review";
        for (first, second) in [(canceled, title), (title, canceled), (canceled, canceled)] {
            let mut obj = heap_meeting(first, second, "ada@example.test");
            obj.extend([0xa5; 24]);
            assert!(main_event(&obj).is_none());
            let mut idx = Index::default();
            idx.observe(&envelope(&obj));
            let ev = idx.events.values().next().expect("canceled meeting decoded");
            assert_eq!(ev.subject.as_deref(), Some(title));
            assert!(ev.canceled);
        }
    }

    #[test]
    fn recovery_requires_matching_subjects_and_an_organizer_address() {
        for (first, second, organizer) in [
            ("Planning", "Review", "ada@example.test"),
            ("Canceled: Other Planning", "Planning", "ada@example.test"),
            ("Planning", "Planning", "Example Room"),
            ("", "", "ada@example.test"),
        ] {
            let mut obj = heap_meeting(first, second, organizer);
            heap_field(&mut obj, 826, &u16le("Planning\0"));
            obj.extend([0xa5; 8]);
            let mut idx = Index::default();
            idx.observe(&envelope(&obj));
            assert_eq!(idx.identities.len(), 1);
            assert!(idx.events.is_empty());
        }
    }

    #[test]
    fn recovery_rejects_a_misframed_roster_or_an_aliased_subject() {
        let mut obj = heap_meeting("Planning", "Planning", "ada@example.test");
        obj.extend([0xa5; 24]);
        let wrong_len = object::u32_at(&obj, 678).unwrap() + 1;
        let subject_slot: [u8; 8] = obj[866..874].try_into().unwrap();
        let mut misframed = obj.clone();
        misframed[678..682].copy_from_slice(&wrong_len.to_le_bytes());
        let mut aliased = obj;
        aliased[1014..1022].copy_from_slice(&subject_slot);
        for candidate in [misframed, aliased] {
            let mut idx = Index::default();
            idx.observe(&envelope(&candidate));
            assert_eq!(idx.identities.len(), 1);
            assert!(idx.events.is_empty());
        }
    }

    #[test]
    fn recovery_rejects_overlapping_subject_fields() {
        let mut obj = heap_meeting("Planning", "Review", "ada@example.test");
        heap_field(&mut obj, 866, &u16le("Cancelled: Training\0"));
        let suffix = object::u32_at(&obj, 866).unwrap() + u16le("Cancelled: ").len() as u32;
        let length = 0x8000_0000 | u16le("Training\0").len() as u32;
        obj[1014..1018].copy_from_slice(&suffix.to_le_bytes());
        obj[1018..1022].copy_from_slice(&length.to_le_bytes());
        obj.extend([0xa5; 24]);

        let mut idx = Index::default();
        idx.observe(&envelope(&obj));
        assert_eq!(idx.identities.len(), 1);
        assert!(idx.events.is_empty());
    }

    #[test]
    fn recovery_rejects_a_subject_that_points_into_the_location() {
        let mut obj = heap_meeting("Planning", "Training", "ada@example.test");
        heap_field(&mut obj, 826, &u16le("Cancelled: Training\0"));
        let location_slot: [u8; 8] = obj[826..834].try_into().unwrap();
        obj[866..874].copy_from_slice(&location_slot);
        obj.extend([0xa5; 24]);

        let mut idx = Index::default();
        idx.observe(&envelope(&obj));
        assert_eq!(idx.identities.len(), 1);
        assert!(idx.events.is_empty());
    }

    #[test]
    fn recovered_subjects_follow_agenda_cancellation_cases() {
        let cases: Vec<(String, Option<String>)> =
            serde_json::from_str(include_str!("../tests/fixtures/calendar-cancellation.json")).unwrap();
        for (input, stripped) in &cases {
            let title = stripped.as_deref().unwrap_or(input);
            for (first, second) in [(input.as_str(), title), (title, input.as_str())] {
                let mut obj = heap_meeting(first, second, "ada@example.test");
                obj.extend([0xa5; 24]);
                let mut idx = Index::default();
                idx.observe(&envelope(&obj));
                let event = idx.events.values().next()
                    .unwrap_or_else(|| panic!("missing copy for {input:?}"));
                assert_eq!(event.subject.as_deref(), Some(title), "{input:?}");
                assert_eq!(event.canceled, stripped.is_some(), "{input:?}");
            }
        }
    }

    #[test]
    fn main_decodable_layouts_keep_all_their_fields() {
        let attendees = [("Bo Example", "bo@example.test")];
        let mut no_roster = object(true, &[]);
        no_roster.truncate(no_roster.len() - 4);
        let mut two_rosters = heap_meeting("Planning", "Planning", "ada@example.test");
        two_rosters.extend([0, 0]);
        two_rosters.extend([1u32.to_le_bytes().to_vec(), lp("Cy Example"), lp("cy@example.test"), vec![0; 12]].concat());

        for obj in [object(true, &attendees), object(true, &[]), no_roster, two_rosters,
                    object_with(true, "Canceled: Quarterly Site Review", &attendees)] {
            let expected = main_event(&obj).expect("main decoder accepts the fixture");
            let mut idx = Index::default();
            idx.observe(&envelope(&obj));
            assert_eq!(idx.events.len(), 1);
            assert_eq!(idx.events.values().next(), Some(&expected));
        }
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
        assert_eq!(ev.attendees, vec![Attendee { name: "Bo Example".into(), email: "bo@example.test".into(), ..Default::default() }]);
        assert!(!ev.canceled, "no cancellation subject");
        assert!(!ev.occurrence, "no occurrence start at +2");
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


    #[test]
    fn attendee_kind_and_response_codes_decode() {
        // TEST-account invitation 2026-10-28: one required and one optional
        // attendee, neither had answered; Outlook wrote (0, 4, 0) and (1, 4, 0).
        let obj = object_status(true, "Quarterly Site Review", &[
            ("Req Example", "req@example.test", [0, 4, 0]),
            ("Opt Example", "opt@example.test", [1, 4, 0]),
        ]);
        let mut idx = Index::default();
        idx.observe(&envelope(&obj));
        let ev = idx.events.values().next().expect("decoded");
        let got: Vec<_> = ev.attendees.iter().map(|a| (a.kind(), a.response(), a.kind_code, a.response_code)).collect();
        assert_eq!(got, vec![
            (Some("required"), Some("no_response"), 0, 4),
            (Some("optional"), Some("no_response"), 1, 4),
        ]);
    }

    #[test]
    fn unverified_response_codes_stay_unlabelled() {
        let obj = object_status(true, "Quarterly Site Review", &[("Bo Example", "bo@example.test", [7, 1, 0])]);
        let mut idx = Index::default();
        idx.observe(&envelope(&obj));
        let a = &idx.events.values().next().expect("decoded").attendees[0];
        assert_eq!((a.kind(), a.response(), a.kind_code, a.response_code), (None, None, 7, 1),
                   "codes without ground truth keep their raw value and no label");
    }

    #[test]
    fn a_canceled_subject_marks_the_candidate_canceled() {
        let obj = object_with(true, "Canceled: Quarterly Site Review", &[("Bo Example", "bo@example.test")]);
        let mut idx = Index::default();
        idx.observe(&envelope(&obj));
        let ev = idx.events.values().next().expect("decoded");
        assert_eq!(ev.subject.as_deref(), Some("Quarterly Site Review"), "subject keeps its current meaning");
        assert!(ev.canceled, "Outlook's stored cancellation prefix is exported");
    }

    #[test]
    fn a_prefix_on_another_subject_is_not_a_cancellation() {
        let obj = object_with(true, "Canceled: Something Else Quarterly Site Review", &[]);
        let mut idx = Index::default();
        idx.observe(&envelope(&obj));
        assert!(idx.events.values().all(|e| !e.canceled), "only the exact prefixed copy of the subject counts");
    }

    #[test]
    fn an_occurrence_start_at_offset_two_marks_an_occurrence() {
        let mut obj = object(true, &[]);
        let start: [u8; 8] = obj[START..START + 8].try_into().unwrap();
        obj[OCCURRENCE..OCCURRENCE + 8].copy_from_slice(&start);
        let mut idx = Index::default();
        idx.observe(&envelope(&obj));
        assert!(idx.events.values().next().expect("decoded").occurrence);
    }
}
