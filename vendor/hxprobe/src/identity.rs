//! Compare selected structural candidates without disclosing their values.
use crate::{folder, observe};
use std::{ffi::OsStr, fs, num::NonZeroU64, os::unix::fs::MetadataExt};

struct Coordinate {
    block: usize,
    object: usize,
}

impl Coordinate {
    fn parse(value: &OsStr) -> Result<Self, ()> {
        let (block, object) = value.to_str().ok_or(())?.split_once(':').ok_or(())?;
        let number = |s: &str| {
            if s.is_empty() || !s.bytes().all(|c| c.is_ascii_digit()) {
                return Err(());
            }
            s.parse().map_err(|_| ())
        };
        Ok(Self {
            block: number(block)?,
            object: number(object)?,
        })
    }
}

#[derive(PartialEq)]
enum Class {
    Message,
    Metadata,
}

impl Class {
    fn label(&self) -> &'static str {
        match self {
            Self::Message => "00ca",
            Self::Metadata => "00bf",
        }
    }
}

struct Candidate {
    class: Class,
    key: NonZeroU64,
    tail: Option<[u8; 51]>,
}

fn candidate(data: &[u8], at: usize) -> Option<Candidate> {
    if let Some(key) = folder::message_key(data.get(at..)?, 0x00ca) {
        return Some(Candidate {
            class: Class::Message,
            key: NonZeroU64::new(key)?,
            tail: None,
        });
    }
    let (_, object) = folder::envelopes(data, folder::METADATA)
        .into_iter()
        .find(|(start, _)| *start == at)?;
    Some(Candidate {
        class: Class::Metadata,
        key: NonZeroU64::new(folder::message_key(object, 0x00bf)?)?,
        tail: folder::metadata_tail(object).copied(),
    })
}

fn stamp(path: &OsStr) -> Result<(u64, u64, u64, i64, i64, i64, i64), ()> {
    let m = fs::metadata(path).map_err(|_| ())?;
    if !m.is_file() {
        return Err(());
    }
    Ok((
        m.dev(),
        m.ino(),
        m.len(),
        m.mtime(),
        m.mtime_nsec(),
        m.ctime(),
        m.ctime_nsec(),
    ))
}

fn read(path: &OsStr, coordinate: Coordinate) -> Result<Candidate, ()> {
    let before = stamp(path)?;
    let raw = fs::read(path).map_err(|_| ())?;
    let mut selected = None;
    observe::verified_blocks(&raw, |off, _, block| {
        if off == coordinate.block {
            selected = candidate(&block.data, coordinate.object);
        }
    })
    .map_err(|_| ())?;
    if stamp(path)? != before {
        return Err(());
    }
    selected.ok_or(())
}

pub fn run(
    before: &OsStr,
    before_at: &OsStr,
    after: &OsStr,
    after_at: &OsStr,
) -> Result<String, ()> {
    let before = read(before, Coordinate::parse(before_at)?)?;
    let after = read(after, Coordinate::parse(after_at)?)?;
    if before.class != after.class {
        return Err(());
    }
    let tail = match (before.tail, after.tail) {
        (Some(a), Some(b)) => (a == b).to_string(),
        _ => "null".into(),
    };
    Ok(format!("{{\"schema\":\"hxstore-identity-comparison/v1\",\"interpretation\":\"candidates_only_no_logical_identity_claim\",\"object_class\":\"{}\",\"selectors\":{{\"local_key\":{{\"object_offsets\":[10,30,38],\"wire_type\":\"u64le\"}},\"metadata_tail\":{{\"offset_from_object_end\":59,\"length\":51}}}},\"local_key_equal\":{},\"metadata_tail_equal\":{tail}}}", before.class.label(), before.key == after.key))
}
