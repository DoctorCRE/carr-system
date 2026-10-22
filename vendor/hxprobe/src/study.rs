//! Structural candidates from verified blocks only. No payload values reach output.
use crate::{
    block,
    locate::{ranges, stored},
    observe,
};
use std::{collections::BTreeMap, ffi::OsStr, fs};

struct Snapshot {
    raw: Vec<u8>,
    blocks: BTreeMap<usize, block::Block>,
    signatures: usize,
    spans: BTreeMap<usize, Vec<observe::Span>>,
    exclusions: BTreeMap<&'static str, usize>,
    unframed_bytes: usize,
    unverified_bytes: usize,
}

impl Snapshot {
    fn read(path: &OsStr) -> Result<Self, ()> {
        let raw = fs::read(path).map_err(|_| ())?;
        let mut blocks = BTreeMap::new();
        let (signatures, issues) = observe::verified_blocks(&raw, |off, _, block| {
            blocks.insert(off, block);
        })
        .map_err(|_| ())?;
        let mut exclusions = BTreeMap::new();
        for issue in &issues {
            *exclusions.entry(issue.reason_code).or_default() += 1;
        }
        let mut spans = BTreeMap::new();
        let mut unframed_bytes = 0;
        let mut verified_file_bytes = 0;
        for (&off, block) in &blocks {
            verified_file_bytes += (off + stored(&raw, off).len()).saturating_sub(off.max(64));
            let partition = match observe::partition(&block.data) {
                Ok(partition) => partition,
                Err(()) => {
                    *exclusions.entry("invalid_object_bounds").or_default() += 1;
                    unframed_bytes += block.data.len();
                    spans.insert(off, Vec::new());
                    continue;
                }
            };
            for span in &partition {
                if span.kind == "unframed" {
                    *exclusions.entry("unframed").or_default() += 1;
                    unframed_bytes += span.end - span.start;
                }
            }
            spans.insert(
                off,
                partition
                    .into_iter()
                    .filter(|s| s.kind != "unframed")
                    .collect(),
            );
        }
        let unverified_bytes = raw.len() - 64 - verified_file_bytes;
        Ok(Self {
            raw,
            blocks,
            signatures,
            spans,
            exclusions,
            unframed_bytes,
            unverified_bytes,
        })
    }

    fn coverage(&self) -> String {
        let exclusions = self
            .exclusions
            .iter()
            .map(|(reason, count)| format!("\"{reason}\":{count}"))
            .collect::<Vec<_>>()
            .join(",");
        format!("{{\"block_signatures\":{},\"verified_blocks\":{},\"unverified_blocks\":{},\"verified_payload_bytes\":{},\"typed_records\":{},\"unframed_payload_bytes\":{},\"unverified_file_bytes\":{},\"exclusions\":{{{exclusions}}}}}",
            self.signatures, self.blocks.len(), self.signatures - self.blocks.len(),
            self.blocks.values().map(|b| b.data.len()).sum::<usize>(),
            self.spans.values().map(Vec::len).sum::<usize>(), self.unframed_bytes, self.unverified_bytes)
    }

    fn records(&self, off: usize, diff: &[(usize, usize)]) -> Vec<String> {
        self.spans
            .get(&off)
            .into_iter()
            .flatten()
            .filter(|s| diff.iter().any(|(a, b)| s.start < *b && *a < s.end))
            .map(|s| {
                format!(
                    "{{\"record_type\":\"{}\",\"record_id\":\"{off}:{}:{}\"}}",
                    s.kind, s.start, s.end
                )
            })
            .collect()
    }

    fn unframed_ranges(&self, off: usize) -> Vec<(usize, usize)> {
        let Some(block) = self.blocks.get(&off) else {
            return Vec::new();
        };
        let mut result = Vec::new();
        let mut cursor = 0;
        for span in &self.spans[&off] {
            if cursor < span.start {
                result.push((cursor, span.start));
            }
            cursor = span.end;
        }
        if cursor < block.data.len() {
            result.push((cursor, block.data.len()));
        }
        result
    }
}

pub fn run(before: &OsStr, after: &OsStr) -> Result<String, ()> {
    let before = Snapshot::read(before)?;
    let after = Snapshot::read(after)?;
    let file_diff = ranges(&before.raw, &after.raw);
    let mut counts = BTreeMap::from([
        ("file_changed_ranges", file_diff.len()),
        (
            "file_changed_bytes",
            file_diff.iter().map(|(a, b)| b - a).sum(),
        ),
        ("changed_blocks", 0),
        ("added_blocks", 0),
        ("removed_blocks", 0),
        ("payload_changed_ranges", 0),
        ("payload_changed_bytes", 0),
        ("unverified_file_changed_bytes", 0),
        ("unframed_payload_changed_bytes", 0),
    ]);
    let offsets = before
        .blocks
        .keys()
        .chain(after.blocks.keys())
        .copied()
        .collect::<std::collections::BTreeSet<_>>();
    let mut common_verified = Vec::new();
    let mut old_records = Vec::new();
    let mut new_records = Vec::new();
    for off in offsets {
        let old = before.blocks.get(&off);
        let new = after.blocks.get(&off);
        let raw_old = old.map_or(&[][..], |_| stored(&before.raw, off));
        let raw_new = new.map_or(&[][..], |_| stored(&after.raw, off));
        if old.is_some() && new.is_some() {
            common_verified.push((off, off + raw_old.len().min(raw_new.len())));
        }
        if raw_old == raw_new {
            continue;
        }
        *counts.get_mut("changed_blocks").unwrap() += 1;
        *counts.get_mut("added_blocks").unwrap() += usize::from(old.is_none());
        *counts.get_mut("removed_blocks").unwrap() += usize::from(new.is_none());
        let a = old.map_or(&[][..], |b| b.data.as_slice());
        let b = new.map_or(&[][..], |b| b.data.as_slice());
        let diff = ranges(a, b);
        *counts.get_mut("payload_changed_ranges").unwrap() += diff.len();
        *counts.get_mut("payload_changed_bytes").unwrap() +=
            diff.iter().map(|(a, b)| b - a).sum::<usize>();
        let mut unframed = before.unframed_ranges(off);
        unframed.extend(after.unframed_ranges(off));
        unframed.sort_unstable();
        let mut end = 0;
        for (start, stop) in unframed {
            let start = start.max(end);
            if start < stop {
                *counts.get_mut("unframed_payload_changed_bytes").unwrap() += diff
                    .iter()
                    .map(|&(a, b)| b.min(stop).saturating_sub(a.max(start)))
                    .sum::<usize>();
            }
            end = end.max(stop);
        }
        old_records.extend(before.records(off, &diff));
        new_records.extend(after.records(off, &diff));
    }
    // Subtract shared verified physical spans, without inspecting every changed byte.
    let mut common = common_verified.iter().peekable();
    let mut unverified_changes = 0;
    for &(a, b) in &file_diff {
        let a = a.max(64);
        let mut verified_changes = 0;
        while let Some(&&(x, y)) = common.peek() {
            if y <= a {
                common.next();
                continue;
            }
            if x >= b {
                break;
            }
            verified_changes += b.min(y).saturating_sub(a.max(x));
            if y > b {
                break;
            }
            common.next();
        }
        unverified_changes += b.saturating_sub(a) - verified_changes;
    }
    *counts.get_mut("unverified_file_changed_bytes").unwrap() = unverified_changes;
    counts.insert("records_before", old_records.len());
    counts.insert("records_after", new_records.len());
    let complete = before.exclusions.is_empty()
        && after.exclusions.is_empty()
        && before.unverified_bytes == 0
        && after.unverified_bytes == 0
        && unverified_changes == 0;
    let counts = counts
        .iter()
        .map(|(key, value)| format!("\"{key}\":{value}"))
        .collect::<Vec<_>>()
        .join(",");
    Ok(format!("{{\"schema\":\"hxstore-study-diff/v1\",\"interpretation\":\"candidates_only_no_causal_or_field_mapping_claim\",\"counts\":{{{counts}}},\"coverage\":{{\"interpretation\":\"verified_block_and_record_framing_only\",\"complete\":{complete},\"before\":{},\"after\":{}}},\"records_before\":[{}],\"records_after\":[{}]}}", before.coverage(), after.coverage(), old_records.join(","), new_records.join(",")))
}
