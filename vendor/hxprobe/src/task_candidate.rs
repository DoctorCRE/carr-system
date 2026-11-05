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

#[derive(Clone, Debug, Serialize)]
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
    decode_status: DecodeStatus,
    reason_codes: Vec<&'static str>,
}

#[derive(Default)]
struct Evidence {
    details: detail::Index,
    task_classifier: bool,
    unsupported: bool,
    mail_verified: bool,
    classifier_ambiguous: bool,
    conflicts: BTreeSet<&'static str>,
}

type Counts = BTreeMap<&'static str, u64>;
fn add(counts: &mut Counts, key: &'static str) {
    *counts.entry(key).or_default() += 1;
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

fn due(db: &Connection, value: Option<Option<i64>>) -> Field<Due> {
    match value {
        Some(Some(seconds)) if seconds % 86400 == 0 => Field::Known(Due::DateOnly {
            date: date(db, seconds),
        }),
        Some(None) => Field::Absent,
        _ => Field::Unknown("date_semantics_unverified"),
    }
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
            c.due = if evidence.conflicts.contains("due") {
                Field::Conflict
            } else {
                due(db, d.due_unix)
            };
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
                ("due", 294, 8, "dotnet_ticks_or_no_date"),
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
    let mut counts = Counts::from([
        ("candidates_seen", 0),
        ("candidates_supported", 0),
        ("candidates_held", 0),
        ("candidates_admitted", 0),
        ("reviews_pending", 0),
        ("writes_acknowledged", 0),
        ("candidates_deferred", 0),
        ("account_unknown", 0),
        ("foreign_account_excluded", 0),
        ("unreadable_candidates", 0),
        ("unknown_tag_class", 0),
        ("mail_classifiers", 0),
        ("task_classifier_hypotheses", 0),
        ("calendar_objects", 0),
        ("blocks_verified", 0),
        ("block_failures", 0),
        ("revisions_conflicted", 0),
        ("decoder_incomplete", 0),
        ("selected_account_objects", 0),
    ]);
    let mut deferred_min = u64::MAX;
    let mut held: BTreeMap<(u64, Kind, u8), Evidence> = BTreeMap::new();
    let (signatures, issues) = observe::verified_blocks(&raw, |_, _, block| {
        add(&mut counts, "blocks_verified");
        let data = &block.data;
        for start in memchr::memchr_iter(5, data) {
            if data.get(start..start + 2) != Some(&[5, 0]) {
                continue;
            }
            let Some(header) = data.get(start..start.saturating_add(10)) else {
                add(&mut counts, "unreadable_candidates");
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
                add(&mut counts, "unreadable_candidates");
                continue;
            };
            let Some(class) = object::u16_at(body, 0) else {
                add(&mut counts, "unreadable_candidates");
                continue;
            };
            if !known(tag, class) {
                add(&mut counts, "unknown_tag_class");
                continue;
            }
            if (tag, class) == (0x0455, 0x006b) {
                add(&mut counts, "calendar_objects");
                add(&mut counts, "account_unknown");
                continue;
            }
            if (tag, class) != (0x060f, 0x00c9) {
                continue;
            }
            let Some((key, owner)) = object::reference(body, 0xc9, 212).filter(|(_, a)| *a > 0)
            else {
                add(&mut counts, "account_unknown");
                continue;
            };
            if owner != account {
                add(&mut counts, "foreign_account_excluded");
                continue;
            }
            add(&mut counts, "selected_account_objects");
            let is_revision = message::is_revision(body);
            let (kind, layout) = if is_revision {
                let classifier = object::Object::new(body, 1541)
                    .and_then(|o| o.slot(1226))
                    .flatten()
                    .and_then(object::text);
                match classifier.as_deref() {
                    Some(s) if s.starts_with("IPM.Note") => {
                        add(&mut counts, "mail_classifiers");
                        continue;
                    }
                    Some("IPM.Task") => {
                        add(&mut counts, "task_classifier_hypotheses");
                        (Kind::StandaloneTask, 0)
                    }
                    _ => {
                        add(&mut counts, "unreadable_candidates");
                        continue;
                    }
                }
            } else {
                if body.len() >= 1541 && body.get(1491) != Some(&3) {
                    add(&mut counts, "unreadable_candidates");
                    continue;
                }
                (Kind::MailFlag, body.get(1491).copied().unwrap_or(0))
            };
            add(&mut counts, "candidates_seen");
            if key <= after {
                continue;
            }
            let id = (key, kind, layout);
            if !held.contains_key(&id) && held.len() >= CAP {
                add(&mut counts, "candidates_deferred");
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
                            if new.due_unix != old.due_unix {
                                e.conflicts.insert("due");
                            }
                            if new.flag != old.flag || new.completed_unix != old.completed_unix {
                                e.conflicts.insert("completion");
                            }
                        }
                    }
                }
                e.details.observe(&data[start..start + length]);
                e.unsupported |= e.details.rejected > 0;
            }
        }
    })
    .map_err(|_| ())?;
    if let Some(((last, _, _), _)) = held.last_key_value() {
        if counts["candidates_deferred"] > 0 && deferred_min <= *last {
            let boundary = *last;
            let before = held.len();
            held.retain(|(key, _, _), _| *key < boundary);
            *counts.get_mut("candidates_deferred").unwrap() += (before - held.len()) as u64;
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
    counts.insert("block_failures", issues.len() as u64);
    if signatures == 0
        || !issues.is_empty()
        || [
            "unknown_tag_class",
            "account_unknown",
            "unreadable_candidates",
            "candidates_deferred",
            "calendar_objects",
            "task_classifier_hypotheses",
        ]
        .iter()
        .any(|key| counts[key] > 0)
        || held.values().any(|e| {
            e.unsupported || (!e.task_classifier && (!e.mail_verified || e.classifier_ambiguous))
        })
    {
        counts.insert("decoder_incomplete", 1);
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
            add(&mut counts, "revisions_conflicted");
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
    counts.insert("candidates_held", held.len() as u64);
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
