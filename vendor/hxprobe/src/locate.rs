//! Location metadata only. The Python manifest guard owns campaign validation.
use crate::{block, header, observe};
use std::{collections::BTreeMap, ffi::OsStr, fs};

pub(crate) fn ranges(before: &[u8], after: &[u8]) -> Vec<(usize, usize)> {
    let mut result = Vec::new();
    let mut start = None;
    for i in 0..before.len().max(after.len()) {
        if before.get(i) != after.get(i) {
            start.get_or_insert(i);
        } else if let Some(begin) = start.take() {
            result.push((begin, i));
        }
    }
    if let Some(begin) = start {
        result.push((begin, before.len().max(after.len())));
    }
    result
}

fn range_json(ranges: &[(usize, usize)]) -> String {
    ranges
        .iter()
        .map(|(a, b)| format!("[{a},{b}]"))
        .collect::<Vec<_>>()
        .join(",")
}

fn blocks(data: &[u8]) -> Result<BTreeMap<usize, block::Block>, ()> {
    let h = header::check(data).map_err(|_| ())?;
    if !h.known || h.page_size != 4096 {
        return Err(());
    }
    let mut result = BTreeMap::new();
    let mut end = 0;
    for off in block::find_all(data) {
        if off < end {
            continue;
        }
        // Synthetic location requires complete verified input, unlike count-only observation.
        let b = block::parse_checked(data, off).map_err(|_| ())?;
        end = off
            + block::PAYLOAD
            + u32::from_le_bytes(data[off + 20..off + 24].try_into().unwrap()) as usize;
        result.insert(off, b);
    }
    Ok(result)
}

fn partition(data: &[u8]) -> Vec<observe::Span> {
    observe::partition(data).unwrap_or_else(|_| {
        vec![observe::Span {
            kind: "unframed".into(),
            start: 0,
            end: data.len(),
            decoded: 0,
        }]
    })
}

fn changed_records(data: &[u8], diff: &[(usize, usize)]) -> String {
    partition(data)
        .iter()
        .filter(|s| diff.iter().any(|(a, b)| s.start < *b && *a < s.end))
        .map(|s| {
            format!(
                "{{\"record_type\":\"{}\",\"start\":{},\"end\":{}}}",
                s.kind, s.start, s.end
            )
        })
        .collect::<Vec<_>>()
        .join(",")
}

pub(crate) fn stored(data: &[u8], off: usize) -> &[u8] {
    let length = u32::from_le_bytes(data[off + 20..off + 24].try_into().unwrap()) as usize;
    &data[off..off + block::PAYLOAD + length]
}

pub fn run(before: &OsStr, after: &OsStr, needles: &OsStr) -> Result<String, ()> {
    let needles = fs::read_to_string(needles).map_err(|_| ())?;
    let needles: Vec<Vec<u8>> = needles
        .lines()
        .map(|line| {
            if line.is_empty() || line.len() % 2 != 0 || !line.is_ascii() {
                return Err(());
            }
            (0..line.len())
                .step_by(2)
                .map(|n| u8::from_str_radix(&line[n..n + 2], 16).map_err(|_| ()))
                .collect()
        })
        .collect::<Result<_, _>>()?;
    if needles.is_empty() {
        return Err(());
    }
    let before = fs::read(before).map_err(|_| ())?;
    let after = fs::read(after).map_err(|_| ())?;
    let old = blocks(&before)?;
    let new = blocks(&after)?;
    let mut hits = Vec::new();
    let mut counts = vec![[0usize; 2]; needles.len()];
    let mut changes = Vec::new();
    for (side, snapshot, blocks) in [(0, "before", &old), (1, "after", &new)] {
        for (off, b) in blocks {
            let spans = partition(&b.data);
            if side == 1 || !new.contains_key(off) {
                let a = old.get(off).map_or(&[][..], |p| p.data.as_slice());
                let z = new.get(off).map_or(&[][..], |p| p.data.as_slice());
                let raw_a = old.get(off).map_or(&[][..], |_| stored(&before, *off));
                let raw_z = new.get(off).map_or(&[][..], |_| stored(&after, *off));
                if raw_a != raw_z {
                    let diff = ranges(a, z);
                    let physical = ranges(raw_a, raw_z)
                        .into_iter()
                        .map(|(a, b)| (a + off, b + off))
                        .collect::<Vec<_>>();
                    let records_before = changed_records(a, &diff);
                    let records_after = changed_records(z, &diff);
                    let status = if side == 0 {
                        "removed"
                    } else if !old.contains_key(off) {
                        "added"
                    } else {
                        "changed"
                    };
                    changes.push(format!("{{\"block_offset\":{off},\"block_type\":{},\"status\":\"{status}\",\"file_ranges\":[{}],\"payload_ranges\":[{}],\"records_before\":[{records_before}],\"records_after\":[{records_after}]}}", b.kind, range_json(&physical), range_json(&diff)));
                }
            }
            for (needle_index, needle) in needles.iter().enumerate() {
                let finder = memchr::memmem::Finder::new(needle);
                let mut cursor = 0;
                while let Some(relative) = finder.find(&b.data[cursor..]) {
                    let pos = cursor + relative;
                    cursor = pos + 1;
                    counts[needle_index][side] += 1;
                    if counts[needle_index][side] > 4096 {
                        continue;
                    }
                    let span = spans
                        .iter()
                        .find(|s| s.start <= pos && pos + needle.len() <= s.end);
                    let kind = span.map_or("cross_record", |s| s.kind.as_str());
                    let relative = span.map_or("null".into(), |s| (pos - s.start).to_string());
                    let object = span
                        .filter(|s| s.kind.starts_with("object.class.") && pos >= s.start + 10)
                        .map_or("null".into(), |s| (pos - s.start - 10).to_string());
                    hits.push(format!("{{\"needle\":{needle_index},\"snapshot\":\"{snapshot}\",\"block_offset\":{off},\"block_type\":{},\"record_type\":\"{kind}\",\"payload_offset\":{pos},\"record_offset\":{relative},\"object_offset\":{object},\"length\":{}}}", b.kind, needle.len()));
                }
            }
        }
    }
    Ok(format!("{{\"file_ranges\":[{}],\"blocks\":[{}],\"hits\":[{}],\"match_counts\":[{}],\"hit_limit_per_needle_per_snapshot\":4096}}",
        range_json(&ranges(&before, &after)), changes.join(","), hits.join(","), counts.iter().map(|n| format!("{{\"before\":{},\"after\":{}}}",n[0],n[1])).collect::<Vec<_>>().join(",")))
}
