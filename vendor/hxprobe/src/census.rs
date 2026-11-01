//! Content-free coverage diagnostics over an explicitly supplied immutable profile copy.
use std::{collections::BTreeMap, ffi::OsStr, fs, path::Path};

use crate::{body, folder, message, observe, Row};

type Counts = BTreeMap<&'static str, usize>;

fn json(counts: &Counts) -> String {
    counts.iter().map(|(k, v)| format!("\"{k}\":{v}")).collect::<Vec<_>>().join(",")
}

pub fn run(path: &OsStr, files: &OsStr, account: u64) -> Result<String, ()> {
    let path = Path::new(path);
    if path.file_name() == Some(OsStr::new("HxStore.hxd")) || path.with_file_name("HxStore.lock").exists()
        || !Path::new(files).is_dir() || fs::symlink_metadata(path).map_err(|_| ())?.file_type().is_symlink() {
        return Err(());
    }
    let before = fs::metadata(path).map_err(|_| ())?;
    let raw = fs::read(path).map_err(|_| ())?;
    let mut objects = message::Index::default();
    let mut folders = folder::Index::default();
    let mut verified = 0;
    let (signatures, issues) = observe::verified_blocks(&raw, |off, _, b| {
        verified += 1;
        objects.observe(off, &b.data);
        folders.observe(off, &b.data);
    }).map_err(|_| ())?;
    let after = fs::metadata(path).map_err(|_| ())?;
    if !issues.is_empty() || signatures == 0 || before.len() != after.len()
        || before.modified().map_err(|_| ())? != after.modified().map_err(|_| ())? {
        return Err(());
    }
    let messages = objects.messages();
    let mut sidecars = body::Sidecars::open(Path::new(files));
    let mut totals = Counts::from([
        ("messages", 0), ("body_complete", 0), ("body_absent", 0), ("body_objects", 0),
        ("body_rejected", 0), ("body_inline", 0), ("body_sidecar", 0),
        ("sidecar_linked", 0), ("sidecar_missing", 0), ("sidecar_unreadable", 0), ("sidecar_decode_failed", 0),
        ("recipients_none", 0), ("recipients_listed", 0), ("recipients_conflict", 0),
        ("recipient_children", 0), ("recipient_objects", 0), ("recipient_rejected", 0),
        ("recipient_to", 0), ("recipient_cc", 0), ("recipient_bcc", 0), ("recipient_unmapped", 0),
        ("subject_missing", 0), ("subject_unset_or_conflict", 0), ("subject_empty", 0),
        ("unknown_folder_messages", 0), ("folder_uncatalogued", 0), ("folder_unset_or_conflict", 0),
        ("folder_account_mismatch", 0), ("folder_unnamed", 0),
    ]);
    let mut rows = Vec::new();
    for (&key, m) in &messages {
        if m.account != Some(account) || !m.item_class.as_deref().is_some_and(|v| v.starts_with("IPM.Note")) {
            continue;
        }
        let row = Row::new(key, m, &mut sidecars, &folders);
        let mut counts = totals.keys().map(|&k| (k, 0)).collect::<Counts>();
        counts.insert("messages", 1);
        counts.insert("body_complete", usize::from(row.html.is_some() && !row.body.trim().is_empty()));
        counts.insert("body_absent", usize::from(m.body_objects == 0));
        counts.insert("body_objects", m.body_objects);
        counts.insert("body_rejected", m.body_rejected);
        for b in &m.bodies {
            match b {
                message::Body::Html(_) => *counts.get_mut("body_inline").unwrap() += 1,
                message::Body::Sidecar(path) => {
                    *counts.get_mut("body_sidecar").unwrap() += 1;
                    let status = match sidecars.resolve(path) {
                        Ok(_) => "sidecar_linked",
                        Err(code) => code,
                    };
                    *counts.get_mut(status).unwrap() += 1;
                }
            }
        }
        counts.insert("recipient_objects", m.recipient_objects);
        counts.insert("recipient_children", m.recipient_children);
        counts.insert("recipient_rejected", m.recipient_rejected);
        let status = match &m.recipients {
            None => "recipients_conflict",
            Some(v) if v.is_empty() => "recipients_none",
            Some(_) => "recipients_listed",
        };
        counts.insert(status, 1);
        for r in m.recipients.iter().flatten() {
            let kind = match r.kind {
                message::TO => "recipient_to", message::CC => "recipient_cc", message::BCC => "recipient_bcc",
                _ => "recipient_unmapped",
            };
            *counts.get_mut(kind).unwrap() += 1;
        }
        if m.subject.as_deref().is_none_or(|v| v.trim().is_empty()) {
            counts.insert("subject_missing", 1);
            counts.insert(if m.subject.is_none() { "subject_unset_or_conflict" } else { "subject_empty" }, 1);
        }
        let folder_status = match m.folder {
            None => Some("folder_unset_or_conflict"),
            Some(id) => match folders.folders.get(&id) {
                None => Some("folder_uncatalogued"),
                Some(f) if f.account_id != account => Some("folder_account_mismatch"),
                Some(f) if f.name.is_none() => Some("folder_unnamed"),
                Some(_) => None,
            },
        };
        if let Some(status) = folder_status {
            counts.insert("unknown_folder_messages", 1);
            counts.insert(status, 1);
        }
        for (&name, n) in &counts { *totals.get_mut(name).unwrap() += n; }
        rows.push(format!("\"{key}\":{{{}}}", json(&counts)));
    }
    totals.insert("blocks_verified", verified);
    totals.insert("message_layout_rejected_all_accounts", objects.rejected(&messages));
    let (orphaned_bodies, orphaned_recipients) = objects.orphaned_children();
    totals.insert("body_orphaned_all_accounts", orphaned_bodies);
    totals.insert("recipient_orphaned_all_accounts", orphaned_recipients);
    totals.insert("body_unlinked_all_accounts", objects.unlinked_bodies);
    totals.insert("recipient_unlinked_all_accounts", objects.unlinked_recipients);
    Ok(format!("{{\"totals\":{{{}}},\"messages\":{{{}}}}}", json(&totals), rows.join(",")))
}
