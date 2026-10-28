#![allow(dead_code)]

#[path = "../fuzz/src/lib.rs"]
mod parsers;
use parsers::{block, lz, record};

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
fn truncated_record_anchor_is_rejected() {
    for anchor in [0, 1] {
        assert!(record::parse(0, &[], anchor).is_none());
    }
}

#[test]
fn overflowing_record_anchor_is_rejected() {
    assert!(record::parse(0, &[], usize::MAX).is_none());
}

#[test]
fn overflowing_field_window_is_bounded_to_input() {
    let bytes = b"x\0a\0b\0c\0\0\0";
    let fields = record::fields(bytes, 2, 0, usize::MAX);
    assert_eq!(fields.len(), 1);
    assert_eq!(fields[0].text, "abc");
    assert!(record::fields(bytes, usize::MAX, 0, 0).is_empty());
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

fn synthetic_record() -> Vec<u8> {
    let mut bytes: Vec<_> = "sender@example.test\0Synthetic Sender\0IPM.Note\0"
        .encode_utf16()
        .flat_map(u16::to_le_bytes)
        .collect();
    bytes.extend_from_slice(b"<html><body>hello world synthetic fixture body text</body></html>");
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
    let anchor = memchr::memmem::find(&bytes, &record::anchor_needle()).unwrap();
    let message = record::parse(0, &bytes, anchor).expect("synthetic record has a valid anchor");
    assert_eq!(message.sender.as_deref(), Some("sender@example.test"));
    assert_eq!(message.body, "hello world synthetic fixture body text");

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
            parsers::fuzz_record(bytes);
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
