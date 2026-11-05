use crate::{detail, message, object, observe};
use memmap2::Mmap;
use rusqlite::{params, Connection};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsString,
    fs,
    path::Path,
};

const CAP: usize = 10_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case")]
enum Kind {
    MailFlag,
    StandaloneTask,
    #[allow(dead_code)]
    CalendarReminder,
}

impl Kind {
    fn as_str(self) -> &'static str {
        match self {
            Self::MailFlag => "mail_flag",
            Self::StandaloneTask => "standalone_task",
            Self::CalendarReminder => "calendar_reminder",
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
enum IdentityBasis {
    VerifiedLocalObject,
    Unresolved,
}

#[derive(PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum DecodeStatus {
    #[allow(dead_code)]
    Supported,
    Partial,
    Unsupported,
    Conflict,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "state", content = "value", rename_all = "snake_case")]
#[allow(dead_code)]
enum Field<T> {
    Known(T),
    Absent,
    Unknown(&'static str),
    Conflict,
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
#[allow(dead_code)]
enum Due {
    DateOnly {
        date: String,
    },
    ZonedInstant {
        rfc3339_utc: String,
        verified_zone: String,
    },
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum Completion {
    Open,
    Completed {
        source_completed_on: Option<String>,
        source_completed_at: Option<String>,
    },
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
#[allow(dead_code)]
enum Deletion {
    Present,
    Tombstone { verified_revision: Revision },
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
#[allow(dead_code)]
enum Reminder {
    Disabled,
    Scheduled {
        at_utc: String,
        zone: String,
        parent_identity: String,
        occurrence_identity: String,
    },
}

#[derive(Clone, Debug, Serialize)]
struct Revision {
    scheme: &'static str,
    value: String,
}

#[derive(Serialize)]
struct Source<'a> {
    store_generation: &'a str,
    account_key: String,
    object_key: Option<String>,
    identity_basis: IdentityBasis,
    format_id: Option<&'static str>,
    outlook_build: &'a str,
    decoder_revision: &'a str,
}

#[derive(Serialize)]
struct Provenance {
    field: &'static str,
    tag: u16,
    class: u16,
    object_offset: usize,
    length: usize,
    encoding: &'static str,
    evidence_id: &'static str,
}

#[derive(Serialize)]
struct UnresolvedSpan {
    object_offset: usize,
    length: usize,
    encoding: &'static str,
    raw_value: Field<String>,
    reason_code: &'static str,
}

#[derive(Serialize)]
struct Candidate<'a> {
    schema: &'static str,
    kind: Kind,
    source: Source<'a>,
    revision: Field<Revision>,
    title: Field<String>,
    due: Field<Due>,
    completion: Field<Completion>,
    deletion: Field<Deletion>,
    reminder: Field<Reminder>,
    provenance: Vec<Provenance>,
    unresolved_spans: Vec<UnresolvedSpan>,
    decode_status: DecodeStatus,
    reason_codes: Vec<&'static str>,
}

#[derive(Default)]
struct Evidence {
    details: detail::Index,
    raw_dates: Option<(u32, [Field<String>; 2])>,
    task_classifier: bool,
    unsupported: bool,
    mail_verified: bool,
    classifier_ambiguous: bool,
    conflicts: BTreeSet<&'static str>,
}

#[derive(Default, Serialize)]
struct Counts {
    candidates_seen: u64,
    candidates_supported: u64,
    candidates_held: u64,
    candidates_admitted: u64,
    reviews_pending: u64,
    writes_acknowledged: u64,
    candidates_deferred: u64,
    account_unknown: u64,
    foreign_account_excluded: u64,
    unreadable_candidates: u64,
    unknown_tag_class: u64,
    mail_classifiers: u64,
    task_classifier_hypotheses: u64,
    calendar_objects: u64,
    blocks_verified: u64,
    block_failures: u64,
    revisions_conflicted: u64,
    decoder_incomplete: u64,
    selected_account_objects: u64,
}

fn uuid(s: &str) -> bool {
    s.len() == 36
        && s.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_hexdigit()
            }
        })
}

fn date(db: &Connection, seconds: i64) -> String {
    db.query_row("SELECT date(?1,'unixepoch')", [seconds], |r| r.get(0))
        .unwrap()
}

fn candidate<'a>(
    db: &Connection,
    key: u64,
    kind: Kind,
    evidence: &Evidence,
    account: u64,
    generation: &'a str,
    build: &'a str,
    revision: &'a str,
) -> Candidate<'a> {
    let mut c = Candidate {
        schema: "outlook-task-candidate/v1",
        kind,
        source: Source {
            store_generation: generation,
            account_key: account.to_string(),
            object_key: None,
            identity_basis: IdentityBasis::Unresolved,
            format_id: None,
            outlook_build: build,
            decoder_revision: revision,
        },
        revision: Field::Unknown("source_order_unverified"),
        title: Field::Unknown("title_unverified"),
        due: Field::Unknown("format_unverified"),
        completion: Field::Unknown("format_unverified"),
        deletion: Field::Unknown("tombstone_unverified"),
        reminder: Field::Unknown("reminder_format_unverified"),
        provenance: vec![],
        unresolved_spans: vec![],
        decode_status: DecodeStatus::Unsupported,
        reason_codes: vec!["admission_disabled"],
    };
    if evidence.task_classifier {
        c.reason_codes.push("task_classifier_hypothesis");
        c.provenance.push(Provenance {
            field: "classifier",
            tag: 0x060f,
            class: 0x00c9,
            object_offset: 1226,
            length: 8,
            encoding: "heap_descriptor_utf16le",
            evidence_id: "message-item-class-not-task-format-proof",
        });
    } else if !evidence.unsupported && evidence.mail_verified && !evidence.classifier_ambiguous {
        if let Some((rev, d)) = evidence.details.details.get(&key) {
            c.source.object_key = Some(key.to_string());
            c.source.identity_basis = IdentityBasis::VerifiedLocalObject;
            c.source.format_id = Some("mail-detail-060f-00c9-layout3");
            c.revision = Field::Known(Revision {
                scheme: "mail_detail_u32",
                value: rev.to_string(),
            });
            c.due = Field::Unknown("date_semantics_unverified");
            c.reason_codes.push("date_semantics_unverified");
            if let Some((_, raw)) = &evidence.raw_dates {
                c.unresolved_spans = [294, 302]
                    .into_iter()
                    .zip(raw)
                    .map(|(offset, value)| UnresolvedSpan {
                        object_offset: offset,
                        length: 8,
                        encoding: "u64le_decimal",
                        raw_value: value.clone(),
                        reason_code: "date_semantics_unverified",
                    })
                    .collect();
            }
            c.completion = if evidence.conflicts.contains("completion") {
                Field::Conflict
            } else {
                match d.flag {
                    Some(detail::Flag::Flagged) => Field::Known(Completion::Open),
                    Some(detail::Flag::Complete) => Field::Known(Completion::Completed {
                        source_completed_on: d
                            .completed_unix
                            .flatten()
                            .filter(|s| s % 86400 == 0)
                            .map(|s| date(db, s)),
                        source_completed_at: None,
                    }),
                    Some(detail::Flag::None) => {
                        c.reason_codes.push("mail_flag_clear");
                        Field::Absent
                    }
                    None => Field::Unknown("flag_value_unverified"),
                }
            };
            c.decode_status = if !evidence.conflicts.is_empty() {
                DecodeStatus::Conflict
            } else {
                DecodeStatus::Partial
            };
            for (field, offset, length, encoding) in [
                ("revision", 1418, 4, "u32le"),
                ("completion", 1474, 1, "flag_byte"),
                ("completion_date", 286, 8, "dotnet_ticks_or_no_date"),
            ] {
                c.provenance.push(Provenance {
                    field,
                    tag: 0x060f,
                    class: 0x00c9,
                    object_offset: offset,
                    length,
                    encoding,
                    evidence_id: "hxstore-atlas-mail-detail",
                });
            }
        }
    } else {
        c.reason_codes.push("layout_or_mail_identity_unverified");
    }
    c
}

fn known(tag: u16, class: u16) -> bool {
    matches!(
        (tag, class),
        (0x04c2 | 0x04d8, 0x004d)
            | (0x0430, 0x004f)
            | (0x02c8 | 0x02b8, 0x00bf)
            | (0x0740 | 0x074e, 0x00ca)
            | (0x060f, 0x00c9)
            | (0x0455, 0x006b)
            | (0x015e, 0x0055)
            | (0x00b4, 0x0069)
            | (0x0318, 0x016a)
    )
}

pub fn run(args: &[OsString]) -> Result<String, ()> {
    if !(args.len() == 7 || args.len() == 8) || args[0] != "--immutable-copy" {
        return Err(());
    }
    let source = Path::new(&args[1]);
    let target = Path::new(&args[2]);
    let account = args[3]
        .to_str()
        .and_then(|s| s.parse::<u64>().ok())
        .filter(|v| *v > 0)
        .ok_or(())?;
    let generation = args[4].to_str().filter(|s| uuid(s)).ok_or(())?;
    let build = args[5]
        .to_str()
        .filter(|s| !s.is_empty() && s.len() <= 100 && !s.chars().any(char::is_control))
        .ok_or(())?;
    let revision = args[6]
        .to_str()
        .filter(|s| s.len() == 40 && s.bytes().all(|b| b.is_ascii_hexdigit()))
        .ok_or(())?;
    let after = if args.len() == 8 {
        args[7]
            .to_str()
            .and_then(|s| s.parse::<u64>().ok())
            .ok_or(())?
    } else {
        0
    };
    if source.file_name().is_some_and(|s| s == "HxStore.hxd")
        || source.with_file_name("HxStore.lock").exists()
        || fs::symlink_metadata(source)
            .map_err(|_| ())?
            .file_type()
            .is_symlink()
        || target.exists()
    {
        return Err(());
    }
    let file = fs::File::open(source).map_err(|_| ())?;
    let before = file.metadata().map_err(|_| ())?;
    let raw = unsafe { Mmap::map(&file) }.map_err(|_| ())?;
    let mut counts = Counts::default();
    let mut deferred_min = u64::MAX;
    let mut held: BTreeMap<(u64, Kind, u8), Evidence> = BTreeMap::new();
    let (signatures, issues) = observe::verified_blocks(&raw, |_, _, block| {
        counts.blocks_verified += 1;
        let data = &block.data;
        for start in memchr::memchr_iter(5, data) {
            if data.get(start..start + 2) != Some(&[5, 0]) {
                continue;
            }
            let Some(header) = data.get(start..start.saturating_add(10)) else {
                counts.unreadable_candidates += 1;
                continue;
            };
            if header[8..10] != [0, 0] {
                continue;
            }
            let tag = object::u16_at(header, 2).unwrap();
            let length = object::u32_at(header, 4).unwrap() as usize;
            let Some(body) = length
                .checked_sub(10)
                .and_then(|len| data.get(start + 10..start + 10 + len))
            else {
                counts.unreadable_candidates += 1;
                continue;
            };
            let Some(class) = object::u16_at(body, 0) else {
                counts.unreadable_candidates += 1;
                continue;
            };
            if !known(tag, class) {
                counts.unknown_tag_class += 1;
                continue;
            }
            if (tag, class) == (0x0455, 0x006b) {
                counts.calendar_objects += 1;
                counts.account_unknown += 1;
                continue;
            }
            if (tag, class) != (0x060f, 0x00c9) {
                continue;
            }
            let Some((key, owner)) = object::reference(body, 0xc9, 212).filter(|(_, a)| *a > 0)
            else {
                counts.account_unknown += 1;
                continue;
            };
            if owner != account {
                counts.foreign_account_excluded += 1;
                continue;
            }
            counts.selected_account_objects += 1;
            let is_revision = message::is_revision(body);
            let (kind, layout) = if is_revision {
                let classifier = object::Object::new(body, 1541)
                    .and_then(|o| o.slot(1226))
                    .flatten()
                    .and_then(object::text);
                match classifier.as_deref() {
                    Some(s) if s.starts_with("IPM.Note") => {
                        counts.mail_classifiers += 1;
                        continue;
                    }
                    Some("IPM.Task") => {
                        counts.task_classifier_hypotheses += 1;
                        (Kind::StandaloneTask, 0)
                    }
                    _ => {
                        counts.unreadable_candidates += 1;
                        continue;
                    }
                }
            } else {
                if body.len() >= 1541 && body.get(1491) != Some(&3) {
                    counts.unreadable_candidates += 1;
                    continue;
                }
                (Kind::MailFlag, body.get(1491).copied().unwrap_or(0))
            };
            counts.candidates_seen += 1;
            if key <= after {
                continue;
            }
            let id = (key, kind, layout);
            if !held.contains_key(&id) && held.len() >= CAP {
                counts.candidates_deferred += 1;
                if held.last_key_value().is_some_and(|(last, _)| *last > id) {
                    if let Some(((removed, _, _), _)) = held.pop_last() {
                        deferred_min = deferred_min.min(removed);
                    }
                } else {
                    deferred_min = deferred_min.min(key);
                    continue;
                }
            }
            let e = held.entry(id).or_default();
            if is_revision {
                e.task_classifier = true;
            } else {
                let mut incoming = detail::Index::default();
                incoming.observe(&data[start..start + length]);
                if let Some((rev, new)) = incoming.details.get(&key) {
                    if let Some((old_rev, old)) = e.details.details.get(&key) {
                        if rev > old_rev {
                            e.conflicts.clear();
                        }
                        if rev == old_rev {
                            if new.flag != old.flag || new.completed_unix != old.completed_unix {
                                e.conflicts.insert("completion");
                            }
                        }
                    }
                }
                if let Some((rev, _)) = incoming.details.get(&key) {
                    let raw = [294, 302].map(|offset| {
                        Field::Known(
                            u64::from_le_bytes(body[offset..offset + 8].try_into().unwrap())
                                .to_string(),
                        )
                    });
                    match &mut e.raw_dates {
                        Some((old_rev, _)) if *old_rev > *rev => {}
                        Some((old_rev, old)) if *old_rev == *rev => {
                            for (held, new) in old.iter_mut().zip(raw) {
                                if *held != new {
                                    *held = Field::Conflict;
                                    e.conflicts.insert("raw_date_slots");
                                }
                            }
                        }
                        _ => e.raw_dates = Some((*rev, raw)),
                    }
                }
                e.details.observe(&data[start..start + length]);
                e.unsupported |= e.details.rejected > 0;
            }
        }
    })
    .map_err(|_| ())?;
    if let Some(((last, _, _), _)) = held.last_key_value() {
        if counts.candidates_deferred > 0 && deferred_min <= *last {
            let boundary = *last;
            let before = held.len();
            held.retain(|(key, _, _), _| *key < boundary);
            counts.candidates_deferred += (before - held.len()) as u64;
        }
    }
    observe::verified_blocks(&raw, |_, _, block| {
        for (_, body) in crate::folder::envelopes(&block.data, message::MESSAGE) {
            let Some((key, owner)) = object::reference(body, 0xc9, 212) else {
                continue;
            };
            if owner != account || !message::is_revision(body) {
                continue;
            }
            let classifier = object::Object::new(body, 1541)
                .and_then(|o| o.slot(1226))
                .flatten()
                .and_then(object::text);
            for ((local_key, kind, _), evidence) in
                held.range_mut((key, Kind::MailFlag, 0)..=(key, Kind::MailFlag, u8::MAX))
            {
                if *local_key != key || *kind != Kind::MailFlag {
                    continue;
                }
                if classifier
                    .as_deref()
                    .is_some_and(|s| s.starts_with("IPM.Note"))
                {
                    evidence.mail_verified = true;
                } else {
                    evidence.classifier_ambiguous = true;
                }
            }
        }
    })
    .map_err(|_| ())?;
    let after_meta = file.metadata().map_err(|_| ())?;
    if before.len() != after_meta.len()
        || before.modified().map_err(|_| ())? != after_meta.modified().map_err(|_| ())?
    {
        return Err(());
    }
    counts.block_failures = issues.len() as u64;
    if signatures == 0
        || !issues.is_empty()
        || counts.unknown_tag_class > 0
        || counts.account_unknown > 0
        || counts.unreadable_candidates > 0
        || counts.candidates_deferred > 0
        || counts.calendar_objects > 0
        || counts.task_classifier_hypotheses > 0
        || held.values().any(|e| {
            e.unsupported || (!e.task_classifier && (!e.mail_verified || e.classifier_ambiguous))
        })
    {
        counts.decoder_incomplete = 1;
    }
    let output = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(target)
        .map_err(|_| ())?;
    drop(output);
    let mut db = Connection::open(target).map_err(|_| ())?;
    let tx = db.transaction().map_err(|_| ())?;
    tx.execute_batch("CREATE TABLE task_candidates (local_key TEXT NOT NULL, kind TEXT NOT NULL, layout INTEGER NOT NULL,
        candidate_json TEXT NOT NULL, PRIMARY KEY(local_key,kind,layout));
        CREATE TABLE task_census (receipt_json TEXT NOT NULL);
        CREATE TABLE task_context (account_key TEXT NOT NULL, store_generation TEXT NOT NULL, resume_after_key TEXT NOT NULL);").map_err(|_| ())?;
    let mut insert = tx
        .prepare("INSERT INTO task_candidates VALUES (?1,?2,?3,?4)")
        .map_err(|_| ())?;
    for ((key, kind, layout), evidence) in &held {
        let c = candidate(
            &tx, *key, *kind, evidence, account, generation, build, revision,
        );
        if c.decode_status == DecodeStatus::Conflict {
            counts.revisions_conflicted += 1;
        }
        let mut value = serde_json::to_value(c).map_err(|_| ())?;
        let digest = format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(&value).map_err(|_| ())?)
        );
        value
            .as_object_mut()
            .unwrap()
            .insert("candidate_digest".into(), Value::String(digest));
        insert
            .execute(params![
                key.to_string(),
                kind.as_str(),
                layout,
                value.to_string()
            ])
            .map_err(|_| ())?;
    }
    drop(insert);
    counts.candidates_held = held.len() as u64;
    let cursor = held
        .last_key_value()
        .map(|((key, _, _), _)| *key)
        .unwrap_or(after);
    let receipt = serde_json::to_string(&counts).map_err(|_| ())?;
    tx.execute("INSERT INTO task_census VALUES (?1)", [&receipt])
        .map_err(|_| ())?;
    tx.execute(
        "INSERT INTO task_context VALUES (?1,?2,?3)",
        params![account.to_string(), generation, cursor.to_string()],
    )
    .map_err(|_| ())?;
    tx.commit().map_err(|_| ())?;
    Ok(receipt)
}
