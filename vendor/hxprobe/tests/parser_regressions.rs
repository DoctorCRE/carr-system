#![allow(dead_code)]

#[path = "../fuzz/src/lib.rs"]
mod parsers;
use parsers::{block, lz, message, object};

#[test]
fn block_offset_overflow_is_rejected() {
    assert!(matches!(
        block::parse_checked(&[], usize::MAX),
        Err(block::ParseError::TruncatedHeader)
    ));
}

#[test]
fn impossible_lz4_output_size_is_rejected_without_allocating() {
    assert!(lz::decode_exact(&[], usize::MAX).is_none());
}

#[test]
fn overflowing_slot_offsets_and_lengths_are_rejected() {
    let mut bytes = vec![0u8; 200];
    bytes[120..124].copy_from_slice(&u32::MAX.to_le_bytes());
    bytes[124..128].copy_from_slice(&u32::MAX.to_le_bytes());
    let o = object::Object::new(&bytes, 100).unwrap();
    assert_eq!(o.slot(120), None);
    assert_eq!(o.slot(usize::MAX), None);
    assert!(object::Object::new(&bytes, usize::MAX).is_none());
    assert_eq!(o.reference(usize::MAX, 0x00c9, 212), None);
}

struct Random(u64);

impl Random {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }

    fn mutate(&mut self, seed: &[u8]) -> Vec<u8> {
        let mut bytes = seed.to_vec();
        for _ in 0..1 + self.next() % 8 {
            let index = self.next() as usize % bytes.len();
            bytes[index] ^= self.next() as u8;
        }
        match self.next() % 4 {
            0 => bytes.truncate(self.next() as usize % (bytes.len() + 1)),
            1 => bytes.extend_from_slice(&self.next().to_le_bytes()),
            _ => {}
        }
        bytes
    }
}

fn checksum(bytes: &[u8]) -> [u8; 4] {
    let mut crc = !0u32;
    for &byte in bytes {
        crc ^= u32::from(byte);
        for _ in 0..8 {
            crc = (crc >> 1) ^ if crc & 1 == 0 { 0 } else { 0xedb8_8320 };
        }
    }
    (!crc).to_le_bytes()
}

fn refresh_checksums(bytes: &mut [u8]) {
    let body = checksum(&bytes[8..]);
    bytes[4..8].copy_from_slice(&body);
    let header = checksum(&bytes[4..32]);
    bytes[..4].copy_from_slice(&header);
}

fn literal_block(payload: &[u8]) -> Vec<u8> {
    let mut compressed = vec![(payload.len().min(15) as u8) << 4];
    if payload.len() >= 15 {
        let mut remaining = payload.len() - 15;
        while remaining >= 255 {
            compressed.push(255);
            remaining -= 255;
        }
        compressed.push(remaining as u8);
    }
    compressed.extend_from_slice(payload);
    let mut bytes = vec![0; block::PAYLOAD];
    bytes[8..16].copy_from_slice(&block::MAGIC.to_le_bytes());
    bytes[16..20].copy_from_slice(&8u32.to_le_bytes());
    bytes[20..24].copy_from_slice(&(compressed.len() as u32).to_le_bytes());
    bytes[24..28].copy_from_slice(&(payload.len() as u32).to_le_bytes());
    bytes[28..32].copy_from_slice(&4u32.to_le_bytes());
    bytes.extend_from_slice(&compressed);
    refresh_checksums(&mut bytes);
    bytes
}

fn utf16(text: &str) -> Vec<u8> {
    text.encode_utf16().chain([0]).flat_map(u16::to_le_bytes).collect()
}

/// An enveloped class 00c9 message object: identity header, sender and ItemClass slots.
fn synthetic_record() -> Vec<u8> {
    let mut obj = vec![0u8; 1541];
    obj[0] = 0xc9;
    for (at, v) in [(10, 70u64), (22, 7), (30, 70), (38, 7)] {
        obj[at..at + 8].copy_from_slice(&v.to_le_bytes());
    }
    obj[18..22].copy_from_slice(&212u32.to_le_bytes());
    let mut heap = Vec::new();
    for (at, value) in [(1386, utf16("sender@example.test")), (1226, utf16("IPM.Note"))] {
        obj[at..at + 4].copy_from_slice(&(heap.len() as u32).to_le_bytes());
        obj[at + 4..at + 8].copy_from_slice(&(value.len() as u32 | 0x8000_0000).to_le_bytes());
        heap.extend(value);
    }
    obj.extend(heap);
    let mut bytes = vec![5, 0, 0x0f, 0x06];
    bytes.extend(((obj.len() + 10) as u32).to_le_bytes());
    bytes.extend([0, 0]);
    bytes.extend(obj);
    bytes
}

#[test]
fn valid_synthetic_block_and_record_keep_decoded_output() {
    let bytes = synthetic_record();
    let block_bytes = literal_block(&bytes);
    assert_eq!(block::find_all(&block_bytes), [0]);
    let decoded = block::parse(&block_bytes, 0).expect("synthetic block passes both CRCs");
    assert_eq!(decoded.kind, 8);
    assert_eq!(decoded.data, bytes);
    let mut index = message::Index::default();
    index.observe(0, &decoded.data);
    let messages = index.messages();
    assert_eq!(messages[&70].sender.as_deref(), Some("sender@example.test"));
    assert_eq!(messages[&70].item_class.as_deref(), Some("IPM.Note"));

    let mut bad_header = block_bytes.clone();
    bad_header[16] ^= 1;
    assert!(matches!(
        block::parse_checked(&bad_header, 0),
        Err(block::ParseError::HeaderCrcMismatch)
    ));
    let mut bad_payload = block_bytes;
    *bad_payload.last_mut().unwrap() ^= 1;
    assert!(matches!(
        block::parse_checked(&bad_payload, 0),
        Err(block::ParseError::PayloadCrcMismatch)
    ));
}

fn run_cases(seed: u64, iterations: usize) {
    let mut random = Random(seed);
    let record_seed = synthetic_record();
    let block_seed = literal_block(&record_seed);
    let lz_seeds: &[&[u8]] = &[
        b"\x50hello",
        b"\x40abcd\x04\x00",
        b"\x10x\x01\x00",
        &[0xf0, 255, 255, 0],
        &[0x1f, b'x', 1, 0, 255, 255, 0],
    ];
    for case in 0..iterations {
        let size = random.next() as usize % 1025;
        let mut arbitrary: Vec<u8> = (0..size).map(|_| random.next() as u8).collect();
        if case % 16 == 0 && arbitrary.len() >= 8 {
            arbitrary[..8].copy_from_slice(&u64::MAX.to_le_bytes());
        }
        let mut mutated_block = random.mutate(&block_seed);
        if case % 2 == 0 && mutated_block.len() >= block::PAYLOAD {
            refresh_checksums(&mut mutated_block);
        }
        let mutated_record = random.mutate(&record_seed);
        let mutated_lz = random.mutate(lz_seeds[case % lz_seeds.len()]);
        for bytes in [&arbitrary, &mutated_block, &mutated_record, &mutated_lz] {
            parsers::fuzz_block(bytes);
            parsers::fuzz_lz4(bytes);
            parsers::fuzz_message(bytes);
        }
    }
    eprintln!("{} inputs per parser, seed {seed:#x}", iterations * 4);
}

#[test]
fn seeded_and_mutated_inputs_do_not_panic() {
    run_cases(0x440_0123_4567_89ab, 8192);
}

#[test]
#[ignore = "extended synthetic fuzz run"]
fn sustained_inputs_do_not_panic() {
    run_cases(0x440_fedc_ba98_7654, 262_144);
}
