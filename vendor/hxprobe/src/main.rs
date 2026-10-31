//! `hxprobe` — a parser for `HxStore.hxd`, the New Outlook for Mac message store.
//!
//! The store is a paged block container: each block carries two CRC-32s and an
//! LZ4-compressed payload holding one or more message records. Because the
//! checksums make every block self-validating, this is a parser rather than a
//! carver — a wrong offset fails loudly instead of yielding plausible garbage.
//!
//! Objects follow one grammar (`object.rs`); each mail field is a fixed slot,
//! catalogued with its evidence in spec/outlook/hxstore-atlas.json. Commands:
//!
//! ```text
//!   hxprobe blocks <file>            verify every block, report coverage
//!   hxprobe db     <file> [out.db] [files-dir]
//!                                    build a SQLite database with FTS5 search;
//!                                    files-dir is the profile's Files folder,
//!                                    for EFMData sidecar bodies
//! ```
//!
//! The file is memory-mapped throughout and never read into an owned buffer, so
//! peak RSS stays near the working set (~67 MB on a 56 MB store) rather than
//! tracking file size.

use std::collections::BTreeMap;
use std::env;
use std::fs::File;

use memmap2::Mmap;

mod attachment;
mod block;
mod body;
mod calendar;
mod category;
mod detail;
mod folder;
mod header;
mod identity;
mod lz;
mod locate;
mod message;
mod object;
mod study;
mod observe;

/// Memory-map a store file read-only.
///
/// Mapping rather than reading matters: these stores reach a gigabyte, and
/// `read()`-ing one into a `Vec` is an easy way to exhaust a machine.
fn map(path: &str) -> Mmap {
    let f = File::open(path).unwrap_or_else(|e| panic!("open {path}: {e}"));
    // SAFETY: the file is opened read-only and only read through the slice.
    // A concurrent writer could tear reads, which is why the documented
    // workflow is to snapshot the store while Outlook is closed.
    let mm = unsafe { Mmap::map(&f) }.unwrap_or_else(|e| panic!("mmap {path}: {e}"));

    // Check the header before doing anything else, so pointing this at the
    // wrong file says so instead of reporting zero blocks.
    match header::check(&mm) {
        Err(e) => {
            eprintln!("{path}: {e}");
            std::process::exit(1);
        }
        Ok(h) if !h.known => {
            eprintln!(
                "{path}: warning: store version {:?} (0x{:02x}) is untested; \
                 known versions are 'i' (macOS) and 'h' (Windows Mail).\n\
                 Parsing anyway: every block is checksummed, so bad data fails \
                 loudly rather than silently.",
                h.version as char, h.version
            );
        }
        Ok(_) => {}
    }
    mm
}

/// Verify every block in the file and report coverage.
fn cmd_blocks(path: &str) {
    let mm = map(path);
    if let Ok(h) = header::check(&mm) {
        println!(
            "Nostromo version {:?}{}, page size {}",
            h.version as char,
            if h.known { "" } else { " (untested)" },
            h.page_size
        );
    }
    let offs = block::find_all(&mm);
    let (mut ok, mut bytes) = (0usize, 0usize);
    let mut by_kind: BTreeMap<u32, usize> = BTreeMap::new();

    for &o in &offs {
        let Some(b) = block::parse(&mm, o) else { continue };
        ok += 1;
        bytes += b.data.len();
        *by_kind.entry(b.kind).or_default() += 1;
    }

    println!("{} block signatures found", offs.len());
    println!(
        "verified {ok}/{} ({:.2}%), {:.1} MB decompressed",
        offs.len(),
        100.0 * ok as f64 / offs.len() as f64,
        bytes as f64 / 1e6
    );
    for (k, n) in &by_kind {
        println!("  type {k:<3} {n}");
    }
}


/// Parse the whole store into a SQLite database with a full-text index.
///
/// One row per mail message object (class 00c9, ItemClass `IPM.Note*`), every field read from
/// its slot. See `message.rs` and spec/outlook/hxstore-atlas.json.
fn cmd_db(path: &str, db_path: &str, files_dir: Option<&str>) {
    let mm = map(path);
    let offs = block::find_all(&mm);
    println!("{} blocks -> {db_path}", offs.len());

    let mut folders = folder::Index::default();
    let mut events = calendar::Index::default();
    let mut attachments = attachment::Index::default();
    let mut objects = message::Index::default();
    let mut categories = category::Index::default();
    let mut details = detail::Index::default();
    let mut sidecars = files_dir.map(|d| body::Sidecars::open(std::path::Path::new(d))).unwrap_or_default();
    let mut blocks_ok = 0usize;
    for &o in &offs {
        if let Some(b) = block::parse(&mm, o) {
            blocks_ok += 1;
            folders.observe(o, &b.data);
            events.observe(&b.data);
            attachments.observe(&b.data);
            objects.observe(o, &b.data);
            categories.observe(&b.data);
            details.observe(&b.data);
        }
    }

    let message_objects = objects.messages();
    let mut rows: Vec<Row> = message_objects
        .iter()
        .filter(|(_, m)| m.item_class.as_deref().is_some_and(|c| c.starts_with("IPM.Note")))
        .map(|(&key, m)| Row::new(key, m, &mut sidecars, &folders))
        .collect();
    rows.sort_by_key(|r| (r.m.sent_unix, r.key));

    for suffix in ["", "-wal", "-shm"] {
        let _ = std::fs::remove_file(format!("{db_path}{suffix}"));
    }
    let mut db = rusqlite::Connection::open(db_path).expect("open db");
    db.execute_batch(
        "PRAGMA journal_mode=WAL;
         PRAGMA foreign_keys=ON;
         CREATE TABLE folders (
             id               INTEGER PRIMARY KEY, -- local store key
             account_id       INTEGER NOT NULL,    -- local account key
             name             TEXT,                -- NULL if revisions disagree
             block            INTEGER NOT NULL,
             -- 'top' (no parent), 'folder' (parent_id is set) or 'unknown'
             -- (no recognized reference, or revisions disagree).
             parent_status    TEXT NOT NULL,
             parent_id        INTEGER              -- may name an unlisted folder
         );
         CREATE TABLE messages (
             id           INTEGER PRIMARY KEY,
             block        INTEGER,   -- file offset of the source block
             sender       TEXT,
             sender_name  TEXT,
             recipients   TEXT,      -- To, Cc and Bcc addresses, comma-separated; order not stored
             subject      TEXT,
             message_id   TEXT,
             body         TEXT,      -- plain text, tags stripped
             html         TEXT,      -- original HTML, when the store has it
             body_words   INTEGER,
             html_bytes   INTEGER,
             -- 'full' when the store holds the whole message, 'preview' when it
             -- holds only Outlook's ~255-character cache of it. Most messages
             -- are preview-only: that is the format, not a parse failure.
             body_kind    TEXT,
             sent_unix    INTEGER,   -- send time (Date), message slot +718
             sent_utc     TEXT,
             subject_inherited INTEGER, -- always 0: subjects are never borrowed
             preview      TEXT,      -- Outlook's own preview of the message
             body_source  TEXT,      -- 'inline' or 'efm' when html is set
             copies       INTEGER,   -- cached revisions of the message object
             copy_key     TEXT,      -- always NULL: rows are store items, not merged copies
             local_keys   TEXT,      -- the message object's store-local key, decimal
             -- From the message object's detail slots; NULL when its newest
             -- revisions disagree.
             flag_status  TEXT,      -- 'none', 'flagged' or 'complete'
             flag_due_unix INTEGER,  -- NULL also when the flag has no due date
             flag_completed_unix INTEGER,
             moved_to_other INTEGER, -- 1 after a one-time move to Other
             pinned       INTEGER,
             received_unix INTEGER,  -- delivery or save-to-folder time, slot +278
             -- 1 when the current folder reference names no catalogued folder of the account
             folder_links_unresolved INTEGER NOT NULL
         );
         CREATE TABLE message_folders (
             message_id INTEGER NOT NULL REFERENCES messages(id),
             folder_id  INTEGER NOT NULL REFERENCES folders(id),
             PRIMARY KEY (message_id, folder_id)
         );
         CREATE INDEX message_folders_folder ON message_folders(folder_id);
         -- Local store keys of each message's objects; attachments name these.
         CREATE TABLE message_keys (
             message_id INTEGER NOT NULL REFERENCES messages(id),
             local_key  INTEGER NOT NULL,
             PRIMARY KEY (message_id, local_key)
         );
         CREATE INDEX message_keys_key ON message_keys(local_key);
         -- Attachment metadata. No bytes: path is the profile-relative cache
         -- file (~/Files/S0/...), present only once Outlook has cached it.
         CREATE TABLE attachments (
             id            INTEGER PRIMARY KEY, -- local store key
             message_key   INTEGER NOT NULL,    -- message_keys.local_key
             name          TEXT NOT NULL,
             name_verified INTEGER NOT NULL,    -- name also stored on its own
             content_type  TEXT,                -- declared by the sender
             content_id    TEXT,                -- nullable MIME part identifier
             size          INTEGER NOT NULL,
             path          TEXT                 -- NULL if revisions disagree
         );
         CREATE INDEX attachments_message ON attachments(message_key);
         -- Categories assigned to a message, limited to defined category names.
         CREATE TABLE message_categories (
             message_id INTEGER NOT NULL REFERENCES messages(id),
             category   TEXT NOT NULL,
             PRIMARY KEY (message_id, category)
         );
         -- Category definitions.
         CREATE TABLE categories (
             id       INTEGER PRIMARY KEY, -- local store key
             owner_id INTEGER NOT NULL,    -- owning list object, not folders.account_id
             name     TEXT,                -- NULL if revisions disagree
             color    INTEGER              -- OlCategoryColor; NULL if revisions disagree
         );
         CREATE INDEX messages_sent ON messages(sent_unix);
         CREATE INDEX messages_sender ON messages(sender);
         CREATE TABLE events (
             id              INTEGER PRIMARY KEY,
             goid            TEXT NOT NULL,     -- Exchange Global Object ID
             start_unix      INTEGER NOT NULL,
             end_unix        INTEGER NOT NULL,
             start_utc       TEXT NOT NULL,
             end_utc         TEXT NOT NULL,
             subject         TEXT,
             location        TEXT,
             organizer_name  TEXT,
             organizer_email TEXT,
             body            TEXT,
             -- 'canceled' (Outlook stored its cancellation subject) or
             -- 'unsupported' (no lifecycle is proven by the bytes).
             lifecycle_status TEXT NOT NULL,
             lifecycle_reason TEXT NOT NULL,
             -- 'occurrence' (one instance of a recurring series) or 'item'.
             recurrence       TEXT NOT NULL
         );
         CREATE TABLE event_attendees (
             event_id      INTEGER NOT NULL REFERENCES events(id),
             name          TEXT,
             email         TEXT,
             kind          TEXT,              -- 'required' / 'optional'; NULL if unverified
             kind_code     INTEGER NOT NULL,
             response      TEXT,              -- 'no_response'; NULL if unverified
             response_code INTEGER NOT NULL
         );
         -- One row per message object key, from what its cached revisions agree on.
         CREATE TABLE message_objects (
             local_key          INTEGER PRIMARY KEY, -- message_keys.local_key
             conversation_id    TEXT,    -- 32 hex: ConversationIndex GUID, the thread
             conversation_depth INTEGER, -- 0 for a thread start, +1 per reply level
             in_reply_to        TEXT,    -- parent Message-ID without angle brackets
             subject            TEXT,    -- '' when the store holds an empty subject
             sender             TEXT,
             -- 'listed' (rows in recipients), 'none' (no recipient objects) or
             -- 'conflict' (cached revisions of a recipient disagree; none exported).
             recipients_status  TEXT NOT NULL
         );
         CREATE TABLE recipients (
             local_key INTEGER NOT NULL REFERENCES message_objects(local_key),
             kind      TEXT,              -- 'to', 'cc', or NULL for an unmapped kind_code
             kind_code INTEGER NOT NULL,
             position  INTEGER NOT NULL,  -- 1-based order within kind_code
             address   TEXT NOT NULL,
             name      TEXT NOT NULL,
             PRIMARY KEY (local_key, kind_code, position)
         );
         -- Each conversation with its message count and latest message.
         CREATE VIEW threads AS
           SELECT o.conversation_id,
                  count(*) AS messages,
                  (SELECT l.local_key FROM message_objects l
                     LEFT JOIN message_keys lk ON lk.local_key = l.local_key
                     LEFT JOIN messages lm ON lm.id = lk.message_id
                    WHERE l.conversation_id = o.conversation_id
                    ORDER BY lm.sent_unix DESC, l.conversation_depth DESC, l.local_key DESC
                    LIMIT 1) AS latest_local_key
             FROM message_objects o
            WHERE o.conversation_id IS NOT NULL
            GROUP BY o.conversation_id;
         -- Counts a caller checks before trusting the export.
         CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
         CREATE VIRTUAL TABLE messages_fts USING fts5(
             sender, sender_name, recipients, subject, body,
             content='messages', content_rowid='id', tokenize='porter unicode61'
         );",
    )
    .expect("schema");

    let stored = rows.len();
    let with_folder = rows.iter().filter(|r| r.folder.is_some()).count();
    println!("  {} folders; {with_folder}/{stored} messages in a catalogued folder", folders.folders.len());
    println!(
        "  {} message objects; {} with a conversation, {} with recipients",
        message_objects.len(),
        message_objects.values().filter(|m| m.conversation_id.is_some()).count(),
        message_objects.values().filter(|m| m.recipients.as_ref().is_some_and(|r| !r.is_empty())).count()
    );
    let tx = db.transaction().expect("tx");
    {
        let mut ins_folder = tx
            .prepare(
                "INSERT INTO folders(id,account_id,name,block,parent_status,parent_id)
                 VALUES (?1,?2,?3,?4,?5,?6)"
            )
            .expect("prepare folders");
        for f in folders.folders.values() {
            let (status, parent) = match f.parent {
                folder::Parent::Top => ("top", None),
                folder::Parent::Folder(id) => ("folder", Some(id as i64)),
                folder::Parent::Unknown => ("unknown", None),
            };
            ins_folder
                .execute(rusqlite::params![f.id as i64, f.account_id as i64, f.name, f.block as i64, status, parent])
                .expect("insert folder");
        }
        let mut ins_link = tx
            .prepare(
                "INSERT INTO message_folders(message_id,folder_id) VALUES (?1,?2)"
            )
            .expect("prepare folder links");
        let mut ins_key = tx
            .prepare("INSERT OR IGNORE INTO message_keys(message_id,local_key) VALUES (?1,?2)")
            .expect("prepare message keys");
        let mut ins_attachment = tx
            .prepare(
                "INSERT INTO attachments(id,message_key,name,name_verified,content_type,size,path,content_id)
                 VALUES (?1,?2,?3,?4,?5,?6,?7,?8)"
            )
            .expect("prepare attachments");
        let mut ins_category = tx
            .prepare("INSERT INTO categories(id,owner_id,name,color) VALUES (?1,?2,?3,?4)")
            .expect("prepare categories");
        for c in categories.categories.values() {
            ins_category
                .execute(rusqlite::params![c.id as i64, c.owner_id as i64, c.name, c.color])
                .expect("insert category");
        }
        for a in attachments.attachments.values() {
            ins_attachment
                .execute(rusqlite::params![a.id as i64, a.message_key as i64, a.name, a.name_verified as i64,
                                           a.content_type, a.size as i64, a.path, a.content_id])
                .expect("insert attachment");
        }
        let mut ins_object = tx
            .prepare(
                "INSERT INTO message_objects(local_key,conversation_id,conversation_depth,in_reply_to,subject,sender,
                                             recipients_status) VALUES (?1,?2,?3,?4,?5,?6,?7)",
            )
            .expect("prepare message objects");
        let mut ins_recipient = tx
            .prepare(
                "INSERT INTO recipients(local_key,kind,kind_code,position,address,name) VALUES (?1,?2,?3,?4,?5,?6)",
            )
            .expect("prepare recipients");
        for (key, m) in &message_objects {
            let status = match &m.recipients {
                None => "conflict",
                Some(r) if r.is_empty() => "none",
                Some(_) => "listed",
            };
            let conversation = m.conversation_id.map(|g| g.iter().map(|b| format!("{b:02x}")).collect::<String>());
            ins_object
                .execute(rusqlite::params![*key as i64, conversation, m.conversation_depth.map(|d| d as i64),
                                           m.in_reply_to, m.subject, m.sender, status])
                .expect("insert message object");
            let mut positions: BTreeMap<u32, i64> = BTreeMap::new();
            for r in m.recipients.iter().flatten() {
                let position = positions.entry(r.kind).or_default();
                *position += 1;
                ins_recipient
                    .execute(rusqlite::params![*key as i64, message::kind_name(r.kind), r.kind, *position,
                                               r.address, r.name])
                    .expect("insert recipient");
            }
        }
        let mut ins = tx
            .prepare(
                "INSERT INTO messages
                   (block,sender,sender_name,recipients,subject,message_id,body,
                    html,body_words,html_bytes,body_kind,sent_unix,sent_utc,
                    subject_inherited,preview,body_source,copies,copy_key,local_keys,
                    flag_status,flag_due_unix,flag_completed_unix,moved_to_other,pinned,received_unix,
                    folder_links_unresolved)
                 VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,
                         datetime(?12,'unixepoch'),0,?13,?14,?15,NULL,?16,?17,?18,?19,?20,?21,?22,?23)",
            )
            .expect("prepare");

        let defined: std::collections::BTreeSet<&str> =
            categories.categories.values().filter_map(|c| c.name.as_deref()).collect();
        let mut ins_message_category = tx
            .prepare("INSERT OR IGNORE INTO message_categories(message_id,category) VALUES (?1,?2)")
            .expect("prepare message categories");
        for r in &rows {
            let m = r.m;
            let d = details.get(r.key);
            let recipients: Vec<&str> = m.recipients.iter().flatten()
                .filter(|p| matches!(p.kind, message::TO | message::CC | message::BCC))
                .map(|p| p.address.as_str())
                .collect();
            ins.execute(rusqlite::params![
                m.block as i64,
                m.sender,
                m.sender_name,
                (!recipients.is_empty()).then(|| recipients.join(", ")),
                m.subject,
                m.message_id,
                r.body,
                r.html,
                r.body.split_whitespace().count() as i64,
                r.html.as_ref().map(|h| h.len() as i64),
                if r.html.is_some() { "full" } else { "preview" },
                m.sent_unix,
                m.preview,
                r.body_source,
                m.revisions as i64,
                r.key.to_string(),
                d.and_then(|d| d.flag).map(detail::Flag::as_str),
                d.and_then(|d| d.due_unix).flatten(),
                d.and_then(|d| d.completed_unix).flatten(),
                d.and_then(|d| d.moved_to_other),
                d.and_then(|d| d.pinned),
                m.received_unix,
                r.folder.is_none() as i64,
            ])
            .expect("insert");
            let message_id = tx.last_insert_rowid();
            for name in d.and_then(|d| d.categories.as_ref()).into_iter().flatten() {
                if defined.contains(name.as_str()) {
                    ins_message_category.execute(rusqlite::params![message_id, name])
                        .expect("insert message category");
                }
            }
            if let Some(folder_id) = r.folder {
                ins_link.execute(rusqlite::params![message_id, folder_id as i64])
                    .expect("insert folder link");
            }
            ins_key.execute(rusqlite::params![message_id, r.key as i64])
                .expect("insert message key");
        }
    }
    {
        let mut ins_event = tx
            .prepare(
                "INSERT INTO events(goid,start_unix,end_unix,start_utc,end_utc,subject,location,
                                    organizer_name,organizer_email,body,lifecycle_status,lifecycle_reason,
                                    recurrence)
                 VALUES (?1,?2,?3,datetime(?2,'unixepoch'),datetime(?3,'unixepoch'),?4,?5,?6,?7,?8,?9,?10,?11)",
            )
            .expect("prepare events");
        let mut ins_attendee = tx
            .prepare(
                "INSERT INTO event_attendees(event_id,name,email,kind,kind_code,response,response_code)
                 VALUES (?1,?2,?3,?4,?5,?6,?7)",
            )
            .expect("prepare attendees");
        for ev in events.events.values() {
            let (status, reason) = if ev.canceled {
                ("canceled", "outlook_canceled_subject")
            } else {
                ("unsupported", "hxstore_lifecycle_unverified")
            };
            let recurrence = if ev.occurrence { "occurrence" } else { "item" };
            ins_event
                .execute(rusqlite::params![
                    ev.goid, ev.start_unix, ev.end_unix, ev.subject, ev.location,
                    ev.organizer_name, ev.organizer_email, ev.body, status, reason, recurrence,
                ])
                .expect("insert event");
            let event_id = tx.last_insert_rowid();
            for a in &ev.attendees {
                ins_attendee
                    .execute(rusqlite::params![
                        event_id, a.name, a.email, a.kind(), a.kind_code, a.response(), a.response_code,
                    ])
                    .expect("insert attendee");
            }
        }
        let mut ins_meta = tx.prepare("INSERT INTO meta(key,value) VALUES (?1,?2)").expect("prepare meta");
        for (key, value) in [
            ("blocks_found", offs.len()),
            ("blocks_verified", blocks_ok),
            ("messages", stored),
            ("messages_full", rows.iter().filter(|r| r.html.is_some()).count()),
            // Message keys the decoder could not read or classify: lost mail, never silently dropped.
            ("messages_rejected", objects.rejected(&message_objects)),
            ("copies_collapsed", rows.iter().map(|r| r.m.revisions - 1).sum()),
            ("local_key_conflicts", 0),
            ("sidecar_files", sidecars.len()),
            ("sidecar_linked", sidecars.linked),
            ("sidecar_rejected", sidecars.rejected),
            ("event_objects", events.objects),
            ("event_identities", events.identities.len()),
            ("events_decoded", events.events.len()),
            ("calendar_lifecycle_unsupported", events.events.values().filter(|e| !e.canceled).count()),
            ("calendar_lifecycle_canceled", events.events.values().filter(|e| e.canceled).count()),
            ("event_occurrences", events.events.values().filter(|e| e.occurrence).count()),
            ("folders", folders.folders.len()),
            ("messages_with_folder", with_folder),
            ("attachment_objects", attachments.objects),
            ("attachments", attachments.attachments.len()),
            ("category_objects", categories.objects),
            ("categories", categories.categories.len()),
            ("detail_objects", details.objects),
            ("details_rejected", details.rejected),
            ("messages_with_detail", rows.iter().filter(|r| details.get(r.key).is_some()).count()),
            ("messages_with_key", stored),
            ("message_objects", message_objects.len()),
            ("message_objects_with_conversation",
             message_objects.values().filter(|m| m.conversation_id.is_some()).count()),
            ("message_objects_with_recipients",
             message_objects.values().filter(|m| m.recipients.as_ref().is_some_and(|r| !r.is_empty())).count()),
            ("recipients", message_objects.values().flat_map(|m| m.recipients.iter().flatten()).count()),
            ("recipient_conflicts", message_objects.values().filter(|m| m.recipients.is_none()).count()),
        ] {
            ins_meta.execute(rusqlite::params![key, value as i64]).expect("insert meta");
        }
    }
    println!(
        "  {} calendar objects; {} canceled and {} unsupported candidates across {} GOID/start groups",
        events.objects,
        events.events.values().filter(|e| e.canceled).count(),
        events.events.values().filter(|e| !e.canceled).count(),
        events.identities.len()
    );
    tx.commit().expect("commit");

    db.execute_batch(
        "INSERT INTO messages_fts(rowid,sender,sender_name,recipients,subject,body)
           SELECT id,sender,sender_name,recipients,subject,body FROM messages;
         INSERT INTO messages_fts(messages_fts) VALUES('optimize');
         VACUUM;",
    )
    .expect("index");

    println!("\n{blocks_ok}/{} blocks verified, {stored} messages stored", offs.len());
    for (label, sql) in [
        ("sender     ", "sender IS NOT NULL"),
        ("date       ", "sent_unix IS NOT NULL"),
        ("sender name", "sender_name IS NOT NULL"),
        ("subject    ", "subject IS NOT NULL"),
        ("message-id ", "message_id IS NOT NULL"),
        ("body       ", "body_words > 3"),
        ("full HTML  ", "body_kind = 'full'"),
    ] {
        let n: i64 = db
            .query_row(&format!("SELECT count(*) FROM messages WHERE {sql}"), [], |r| r.get(0))
            .unwrap_or(0);
        println!("  {label} {n:>6}  {:>5.1}%", n as f64 * 100.0 / stored as f64);
    }
}



const USAGE: &str = "\
hxprobe — parse HxStore.hxd, the New Outlook for Mac message store

usage:
  hxprobe observe <copy>             emit count-only structural JSON
  hxprobe blocks <file>              verify every block, report coverage
  hxprobe db     <file> [out.db] [files-dir]
                                     build a SQLite database (default mail.db);
                                     files-dir reads EFMData sidecar bodies

Work on a snapshot, not the live store: Outlook holds HxStore.lock and
rewrites the file while it runs.";

fn main() {
    let raw: Vec<_> = env::args_os().collect();
    if raw.get(1).is_some_and(|arg| arg == "identity-compare") {
        let result = if raw.len() == 7 && raw[2] == "--immutable-copies" {
            identity::run(&raw[3], &raw[4], &raw[5], &raw[6])
        } else {
            Err(())
        };
        match result {
            Ok(json) => println!("{json}"),
            Err(()) => {
                eprintln!("identity comparison input rejected");
                std::process::exit(2);
            }
        }
        return;
    }
    if raw.get(1).is_some_and(|arg| arg == "study-diff") {
        let result = if raw.len() == 4 { study::run(&raw[2], &raw[3]) } else { Err(()) };
        match result {
            Ok(json) => println!("{json}"),
            Err(()) => {
                eprintln!("study input rejected");
                std::process::exit(2);
            }
        }
        return;
    }
    if raw.get(1).is_some_and(|arg| arg == "locate") {
        let result = if raw.len() == 5 { locate::run(&raw[2], &raw[3], &raw[4]) } else { Err(()) };
        match result {
            Ok(json) => println!("{json}"),
            Err(()) => {
                eprintln!("locator input rejected");
                std::process::exit(2);
            }
        }
        return;
    }
    if raw.get(1).is_some_and(|arg| arg == "observe") {
        let result = if raw.len() == 3 { observe::run(&raw[2]) } else {
            Err(observe::Issue::input("arguments", "invalid_arguments"))
        };
        match result {
            Ok(json) => println!("{json}"),
            Err(issue) => {
                eprintln!("{}", issue.json());
                std::process::exit(2);
            }
        }
        return;
    }
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        eprintln!("{USAGE}");
        std::process::exit(2);
    }
    match args[1].as_str() {
        "blocks" => cmd_blocks(&args[2]),
        "db" => cmd_db(
            &args[2],
            args.get(3).map(String::as_str).unwrap_or("mail.db"),
            args.get(4).map(String::as_str),
        ),
        other => {
            eprintln!("unknown command {other:?}\n\n{USAGE}");
            std::process::exit(2);
        }
    }
}

/// One exported message row: the decoded object plus its resolved body and folder.
struct Row<'a> {
    key: u64,
    m: &'a message::Message,
    html: Option<String>,
    body: String,
    body_source: Option<&'static str>,
    folder: Option<u64>,
}

impl<'a> Row<'a> {
    fn new(key: u64, m: &'a message::Message, sidecars: &mut body::Sidecars, folders: &folder::Index) -> Self {
        let mut found = None;
        for b in &m.bodies {
            found = match b {
                message::Body::Html(html) => Some((html.clone(), "inline")),
                message::Body::Sidecar(path) => sidecars.html(path).map(|html| (html, "efm")),
            };
            if found.is_some() {
                break;
            }
        }
        let (html, body_source) = found.map_or((None, None), |(h, s)| (Some(h), Some(s)));
        let mut body = html.as_deref().map(body::html_to_text).unwrap_or_default();
        if body.split_whitespace().count() < 3 {
            body = m.preview.clone().unwrap_or(body);
        }
        // The folder must be a catalogued folder of the message's own account.
        let folder = m.folder.filter(|f| folders.folders.get(f).is_some_and(|x| Some(x.account_id) == m.account));
        Row { key, m, html, body, body_source, folder }
    }
}
