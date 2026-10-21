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
}

impl Counts {
    fn add(&mut self, total: usize, decoded: usize) {
        self.occurrences += 1;
        self.unknown_occurrences += usize::from(total > decoded);
        self.total += total;
        self.decoded += decoded;
    }
}

pub fn run(path: &OsStr) -> Result<String, ()> {
    let file = File::open(path).map_err(|_| ())?;
    // SAFETY: the caller supplies an immutable copy; this mapping is read-only.
    let mm = unsafe { Mmap::map(&file) }.map_err(|_| ())?;
    let h = header::check(&mm).map_err(|_| ())?;
    if !h.known || h.page_size != 4096 {
        return Err(());
    }
    let mut blocks: BTreeMap<u32, Counts> = BTreeMap::new();
    let mut records: BTreeMap<String, Counts> = BTreeMap::new();
    let offsets = block::find_all(&mm);
    let mut previous_end = 0;
    for off in offsets {
        if off < previous_end {
            return Err(());
        }
        let b = block::parse(&mm, off).ok_or(())?;
        let compressed = u32::from_le_bytes(mm[off + 20..off + 24].try_into().unwrap()) as usize;
        previous_end = off + block::PAYLOAD + compressed;
        blocks.entry(b.kind).or_default().add(b.data.len(), 0);
        observe_payload(&b.data, &mut records)?;
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
        let fields = if kind.starts_with("object.class.") {
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
    Ok(format!("{{\"schema\":\"hxprobe-observation/v1\",\"blocks\":[{}],\"records\":[{}],\"unknown_fields\":[{}]}}", block_rows.join(","), record_rows.join(","), unknown_rows.join(",")))
}

fn observe_payload(data: &[u8], records: &mut BTreeMap<String, Counts>) -> Result<(), ()> {
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
        observe_gap(&data[gap..cursor], records);
        let class = u16::from_le_bytes(data[cursor + 10..cursor + 12].try_into().unwrap());
        records
            .entry(format!("object.class.{class:04x}"))
            .or_default()
            .add(len, 20);
        cursor = end;
        gap = end;
    }
    observe_gap(&data[gap..], records);
    Ok(())
}

fn observe_gap(data: &[u8], records: &mut BTreeMap<String, Counts>) {
    let needle = crate::record::anchor_needle();
    let anchors: Vec<_> = memchr::memmem::find_iter(data, &needle).collect();
    let leading = anchors.first().copied().unwrap_or(data.len());
    if leading > 0 {
        records
            .entry("unframed".into())
            .or_default()
            .add(leading, 0);
    }
    for (i, &start) in anchors.iter().enumerate() {
        let end = anchors.get(i + 1).copied().unwrap_or(data.len());
        records
            .entry("mail".into())
            .or_default()
            .add(end - start, needle.len());
    }
}
