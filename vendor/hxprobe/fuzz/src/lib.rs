#![allow(dead_code)]

#[path = "../../src/block.rs"]
pub mod block;
#[path = "../../src/lz.rs"]
pub mod lz;
#[path = "../../src/record.rs"]
pub mod record;

fn word(bytes: &[u8]) -> usize {
    let mut value = [0u8; 8];
    for (slot, byte) in value.iter_mut().zip(bytes) {
        *slot = *byte;
    }
    u64::from_le_bytes(value) as usize
}

pub fn fuzz_block(bytes: &[u8]) {
    let _ = block::parse_checked(bytes, word(bytes));
    let _ = block::parse_checked(bytes, 0);
    for offset in block::find_all(bytes) {
        let _ = block::parse(bytes, offset);
    }
}

pub fn fuzz_lz4(bytes: &[u8]) {
    let _ = lz::decode_exact(bytes, word(bytes));
    let _ = lz::decode_exact(bytes, bytes.len());
    let _ = lz::decode_exact(bytes, word(bytes) & 0xffff);
}

pub fn fuzz_record(bytes: &[u8]) {
    let anchor = word(bytes);
    let _ = record::parse(0, bytes, anchor);
    let _ = record::parse(0, bytes, 0);
    let _ = record::fields(
        bytes,
        anchor,
        word(bytes.get(8..).unwrap_or_default()),
        word(bytes.get(16..).unwrap_or_default()),
    );
    for anchor in memchr::memmem::find_iter(bytes, &record::anchor_needle()) {
        let _ = record::parse(0, bytes, anchor);
    }
    let _ = record::read_time(bytes);
    let _ = record::html_to_text(&String::from_utf8_lossy(bytes));
}
