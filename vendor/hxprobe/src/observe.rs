//! Count-only structural observation. No content decoder or store value reaches output.
use std::{collections::BTreeMap, ffi::OsStr, fs::File};

use memmap2::Mmap;

use crate::{block, header};

#[derive(Default)]
struct Counts {
    occurrences: usize,
    unknown_occurrences: usize,
    total: usize,
    decoded: usize,
    fields: BTreeMap<String, usize>,
}

impl Counts {
    fn add(&mut self, total: usize, decoded: usize) {
        self.occurrences += 1;
        self.unknown_occurrences += usize::from(total > decoded);
        self.total += total;
        self.decoded += decoded;
    }
}

pub struct Issue {
    pub stage: &'static str,
    pub reason_code: &'static str,
    pub block_ordinal: Option<usize>,
}

impl Issue {
    pub fn input(stage: &'static str, reason_code: &'static str) -> Self {
        Self {
            stage,
            reason_code,
            block_ordinal: None,
        }
    }

    pub fn json(&self) -> String {
        let ordinal = self.block_ordinal.map_or("null".into(), |n| n.to_string());
        format!(
            "{{\"stage\":\"{}\",\"reason_code\":\"{}\",\"block_ordinal\":{ordinal}}}",
            self.stage, self.reason_code
        )
    }
}

pub fn run(path: &OsStr) -> Result<String, Issue> {
    let file = File::open(path).map_err(|_| Issue::input("input", "unreadable"))?;
    let size = file
        .metadata()
        .map_err(|_| Issue::input("input", "unreadable"))?
        .len();
    if size < 64 {
        return Err(Issue::input("header", "not_store"));
    }
    // SAFETY: the caller supplies an immutable copy; this mapping is read-only.
    let mm = unsafe { Mmap::map(&file) }.map_err(|_| Issue::input("input", "unreadable"))?;
    let h = header::check(&mm).map_err(|_| Issue::input("header", "not_store"))?;
    if !h.known || h.page_size != 4096 {
        return Err(Issue::input("header", "unsupported_format"));
    }
    let mut blocks: BTreeMap<u32, Counts> = BTreeMap::new();
    let mut records: BTreeMap<String, Counts> = BTreeMap::new();
    let offsets = block::find_all(&mm);
    let signatures = offsets.len();
    let mut issues = Vec::new();
    let mut verified = 0;
    let mut verified_bytes = 0;
    let mut undecodable = 0;
    let mut previous_end = 0;
    for (index, off) in offsets.into_iter().enumerate() {
        if off < previous_end {
            issues.push(Issue {
                stage: "block",
                reason_code: "overlapping_candidate",
                block_ordinal: Some(index + 1),
            });
            continue;
        }
        let b = match block::parse_checked(&mm, off) {
            Ok(b) => b,
            Err(error) => {
                issues.push(Issue {
                    stage: "block",
                    reason_code: error.reason_code(),
                    block_ordinal: Some(index + 1),
                });
                continue;
            }
        };
        verified += 1;
        verified_bytes += b.data.len();
        let compressed = u32::from_le_bytes(mm[off + 20..off + 24].try_into().unwrap()) as usize;
        previous_end = off + block::PAYLOAD + compressed;
        blocks.entry(b.kind).or_default().add(b.data.len(), 0);
        let mut block_records = BTreeMap::new();
        if observe_payload(&b.data, &mut block_records).is_err() {
            undecodable += 1;
            issues.push(Issue {
                stage: "record",
                reason_code: "invalid_object_bounds",
                block_ordinal: Some(index + 1),
            });
            records
                .entry("unframed".into())
                .or_default()
                .add(b.data.len(), 0);
            continue;
        }
        for (kind, n) in block_records {
            let total = records.entry(kind).or_default();
            total.occurrences += n.occurrences;
            total.unknown_occurrences += n.unknown_occurrences;
            total.total += n.total;
            total.decoded += n.decoded;
            for (field, count) in n.fields {
                *total.fields.entry(field).or_default() += count;
            }
        }
    }
    let block_rows: Vec<_> = blocks
        .iter()
        .map(|(kind, n)| {
            format!(
                "{{\"block_type\":{kind},\"occurrences\":{},\"payload_bytes\":{}}}",
                n.occurrences, n.total
            )
        })
        .collect();
    let mut unknown = Vec::new();
    let record_rows: Vec<_> = records.iter().map(|(kind, n)| {
        if n.total > n.decoded {
            unknown.push((kind, n));
        }
        let mut fields = if kind.starts_with("object.class.") {
            [
                ("envelope.offset.0", "u16le"),
                ("envelope.offset.2", "u16le"),
                ("envelope.offset.4", "u32le"),
                ("envelope.offset.8", "bytes[2]"),
                ("object.offset.0", "u16le"),
                ("object.offset.2", "bytes[8]"),
            ].iter().map(|(id, wire)| format!(
                "{{\"field_id\":\"{id}\",\"identifier_kind\":\"byte_offset\",\"wire_type\":\"{wire}\",\"occurrences\":{}}}", n.occurrences
            )).collect::<Vec<_>>().join(",")
        } else if kind == "mail" {
            format!("{{\"field_id\":\"mail.selector.ItemClass\",\"identifier_kind\":\"string_run_selector\",\"wire_type\":\"utf16le-run\",\"occurrences\":{}}}", n.occurrences)
        } else {
            String::new()
        };
        for (id, count) in &n.fields {
            let wire = crate::schema::CONFIRMED_SPANS.iter().find(|h| h.field_id == id).unwrap().encoding;
            if !fields.is_empty() { fields.push(','); }
            fields.push_str(&format!("{{\"field_id\":\"{id}\",\"identifier_kind\":\"byte_offset\",\"wire_type\":\"{wire}\",\"occurrences\":{count}}}"));
        }
        let percent = if n.total == 0 { "null".into() } else {
            (100.0 * n.decoded as f64 / n.total as f64).to_string()
        };
        format!("{{\"record_type\":\"{kind}\",\"occurrences\":{},\"total_bytes\":{},\"decoded_bytes\":{},\"decoded_byte_percent\":{percent},\"wire_field_ids\":null,\"decoded_field_id_percent\":null,\"fields\":[{fields}]}}", n.occurrences, n.total, n.decoded)
    }).collect();
    unknown.sort_by(|(ka, a), (kb, b)| {
        b.unknown_occurrences
            .cmp(&a.unknown_occurrences)
            .then(ka.cmp(kb))
    });
    let unknown_rows: Vec<_> = unknown.iter().map(|(kind, n)| format!(
        "{{\"record_type\":\"{kind}\",\"field_id\":null,\"wire_type\":\"unknown\",\"occurrences\":{},\"unknown_bytes\":{}}}", n.unknown_occurrences, n.total - n.decoded
    )).collect();
    let mut issue_counts = BTreeMap::new();
    for issue in &issues {
        *issue_counts
            .entry((issue.stage, issue.reason_code))
            .or_insert(0usize) += 1;
    }
    let issue_rows: Vec<_> = issues.iter().map(Issue::json).collect();
    let count_rows: Vec<_> = issue_counts
        .iter()
        .map(|((stage, reason), count)| {
            format!(
                "{{\"stage\":\"{stage}\",\"reason_code\":\"{reason}\",\"occurrences\":{count}}}"
            )
        })
        .collect();
    let decoded: usize = records.values().map(|n| n.decoded).sum();
    let percent = if verified_bytes == 0 {
        "null".into()
    } else {
        (100.0 * decoded as f64 / verified_bytes as f64).to_string()
    };
    Ok(format!("{{\"schema\":\"hxprobe-observation/v1\",\"blocks\":[{}],\"records\":[{}],\"unknown_fields\":[{}],\"coverage\":{{\"block_signatures\":{signatures},\"verified_blocks\":{verified},\"unverified_blocks\":{},\"undecodable_blocks\":{undecodable},\"verified_payload_bytes\":{verified_bytes},\"decoded_bytes\":{decoded},\"decoded_byte_percent\":{percent}}},\"issues\":[{}],\"issue_counts\":[{}]}}", block_rows.join(","), record_rows.join(","), unknown_rows.join(","), signatures - verified, issue_rows.join(","), count_rows.join(",")))
}

pub struct Span {
    pub kind: String,
    pub start: usize,
    pub end: usize,
    pub decoded: usize,
}

fn observe_payload(data: &[u8], records: &mut BTreeMap<String, Counts>) -> Result<(), ()> {
    for span in partition(data)? {
        let mut ranges = vec![(0, span.decoded)];
        let mut fields = Vec::new();
        let bytes = &data[span.start..span.end];
        for h in crate::schema::CONFIRMED_SPANS
            .iter()
            .filter(|h| h.record_type == span.kind)
        {
            let start = 10 + h.offset;
            if let Some(value) = bytes.get(start..start + h.length) {
                let valid = match h.encoding {
                    "utf-8" => std::str::from_utf8(value).is_ok(),
                    "utf-16le" => char::decode_utf16(
                        value
                            .chunks_exact(2)
                            .map(|b| u16::from_le_bytes([b[0], b[1]])),
                    )
                    .all(|c| c.is_ok()),
                    _ => true,
                };
                if valid {
                    ranges.push((start, start + h.length));
                    fields.push(h.field_id);
                }
            }
        }
        ranges.sort_unstable();
        let mut end = 0;
        let mut decoded = 0;
        for (a, b) in ranges {
            decoded += b.saturating_sub(a.max(end));
            end = end.max(b);
        }
        let counts = records.entry(span.kind).or_default();
        counts.add(span.end - span.start, decoded);
        for field in fields {
            *counts.fields.entry(field.into()).or_default() += 1;
        }
    }
    Ok(())
}

pub fn partition(data: &[u8]) -> Result<Vec<Span>, ()> {
    let mut spans = Vec::new();
    let mut cursor = 0;
    let mut gap = 0;
    while cursor + 10 <= data.len() {
        if data[cursor..cursor + 2] != [5, 0] || data[cursor + 8..cursor + 10] != [0, 0] {
            cursor += 1;
            continue;
        }
        let len = u32::from_le_bytes(data[cursor + 4..cursor + 8].try_into().unwrap()) as usize;
        let end = cursor.checked_add(len).ok_or(())?;
        if len < 20 || end > data.len() {
            return Err(());
        }
        if data[cursor + 12..cursor + 20] != [0; 8] {
            cursor += 1;
            continue;
        }
        partition_gap(data, gap, cursor, &mut spans);
        let class = u16::from_le_bytes(data[cursor + 10..cursor + 12].try_into().unwrap());
        spans.push(Span {
            kind: format!("object.class.{class:04x}"),
            start: cursor,
            end,
            decoded: 20,
        });
        cursor = end;
        gap = end;
    }
    partition_gap(data, gap, data.len(), &mut spans);
    Ok(spans)
}

fn partition_gap(data: &[u8], start: usize, end: usize, spans: &mut Vec<Span>) {
    let needle = crate::record::anchor_needle();
    let anchors: Vec<_> = memchr::memmem::find_iter(&data[start..end], &needle)
        .map(|n| start + n)
        .collect();
    let leading = anchors.first().copied().unwrap_or(end);
    if leading > start {
        spans.push(Span {
            kind: "unframed".into(),
            start,
            end: leading,
            decoded: 0,
        });
    }
    for (i, &start) in anchors.iter().enumerate() {
        let end = anchors.get(i + 1).copied().unwrap_or(end);
        spans.push(Span {
            kind: "mail".into(),
            start,
            end,
            decoded: needle.len(),
        });
    }
}
