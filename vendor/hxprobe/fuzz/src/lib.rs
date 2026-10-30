#![allow(dead_code)]

#[path = "../../src/block.rs"]
pub mod block;
#[path = "../../src/lz.rs"]
pub mod lz;
#[path = "../../src/folder.rs"]
pub mod folder;
#[path = "../../src/message.rs"]
pub mod message;
#[path = "../../src/object.rs"]
pub mod object;

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

pub fn fuzz_message(bytes: &[u8]) {
    let mut index = message::Index::default();
    index.observe(0, bytes);
    let _ = index.messages();
    let at = word(bytes);
    for fixed in [0, 340, 1541, 1860, at] {
        if let Some(o) = object::Object::new(bytes, fixed) {
            let _ = o.slot(at);
            let _ = o.slot(at % 2048);
            let _ = o.reference(at % 2048, 0x00c9, 212);
        }
    }
    let _ = object::text(bytes);
    let _ = object::unix(word(bytes) as u64);
}
