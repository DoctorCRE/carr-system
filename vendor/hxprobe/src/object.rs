//! The serialized object grammar shared by every class (spec/outlook/hxstore-atlas.json, `object`).
//!
//! ```text
//!   +0    class u16, 8 zero bytes
//!   +10   key u64 · +18 kind u32 · +22 owner u64 · +30 key u64 · +38 owner u64
//!   +94   u16: length of the inline region that follows the fixed part
//!   fixed part = envelope tag - 10 bytes; heap base = fixed part + inline length
//!   descriptor = (u32 offset, u32 word), relative to the heap base; word bit 31 = set,
//!                bit 25 = the value is the path of an external file, bits 0-23 = length
//! ```
//!
//! The same 46-byte identity header embedded at a fixed offset is a typed reference to another
//! object. Proven on every object of twelve classes in a real store and on labelled TEST-account
//! changes; a set descriptor that does not fit its object means the layout is not this one.

pub(crate) fn u16_at(data: &[u8], at: usize) -> Option<u16> {
    Some(u16::from_le_bytes(data.get(at..at.checked_add(2)?)?.try_into().ok()?))
}

pub(crate) fn u32_at(data: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_le_bytes(data.get(at..at.checked_add(4)?)?.try_into().ok()?))
}

pub(crate) fn u64_at(data: &[u8], at: usize) -> Option<u64> {
    Some(u64::from_le_bytes(data.get(at..at.checked_add(8)?)?.try_into().ok()?))
}

/// One object of a class whose fixed part is `fixed` bytes.
pub(crate) struct Object<'a> {
    pub data: &'a [u8],
    heap: usize,
}

impl<'a> Object<'a> {
    pub fn new(data: &'a [u8], fixed: usize) -> Option<Self> {
        let heap = fixed.checked_add(u16_at(data, 94)? as usize)?;
        (heap <= data.len()).then_some(Object { data, heap })
    }

    /// `None` when the object is malformed, `Some(None)` when the slot is unset.
    pub fn slot(&self, at: usize) -> Option<Option<&'a [u8]>> {
        let len = u32_at(self.data, at.checked_add(4)?)?;
        if len & 0x8000_0000 == 0 {
            return Some(None);
        }
        let start = self.heap.checked_add(u32_at(self.data, at)? as usize)?;
        let end = start.checked_add((len & 0x00ff_ffff) as usize)?;
        self.data.get(start..end).map(Some)
    }

    /// True when the set slot at `at` holds the path of a file outside the store.
    pub fn external(&self, at: usize) -> bool {
        u32_at(self.data, at + 4).is_some_and(|w| w & 0x8200_0000 == 0x8200_0000)
    }

    pub fn u64(&self, at: usize) -> Option<u64> {
        u64_at(self.data, at)
    }

    /// The typed reference at `at`: `(key, owner)` when it names `class` with a matching kind.
    pub fn reference(&self, at: usize, class: u16, kind: u32) -> Option<(u64, u64)> {
        reference(self.data.get(at..)?, class, kind)
    }
}

/// An identity header: class, eight zero bytes, then key, kind and owner, with key and owner repeated.
pub(crate) fn reference(data: &[u8], class: u16, kind: u32) -> Option<(u64, u64)> {
    if u16_at(data, 0)? != class || data.get(2..10)? != [0; 8] || u32_at(data, 18)? != kind {
        return None;
    }
    let (key, owner) = (u64_at(data, 10)?, u64_at(data, 22)?);
    (key != 0 && Some(key) == u64_at(data, 30) && Some(owner) == u64_at(data, 38)).then_some((key, owner))
}

/// NUL-terminated UTF-16LE. Malformed text is a decode failure, not a value.
pub(crate) fn text(bytes: &[u8]) -> Option<String> {
    if bytes.len() < 2 || bytes.len() % 2 != 0 || !bytes.ends_with(&[0, 0]) {
        return None;
    }
    let units: Vec<u16> = bytes[..bytes.len() - 2].chunks_exact(2).map(|b| u16::from_le_bytes([b[0], b[1]])).collect();
    let s = String::from_utf16(&units).ok()?;
    (!s.contains('\0')).then_some(s)
}

/// .NET ticks (100 ns since 0001-01-01 UTC) between 2000 and 2100, as Unix seconds.
pub(crate) fn unix(ticks: u64) -> Option<i64> {
    const EPOCH: u64 = 621_355_968_000_000_000;
    const RANGE: std::ops::Range<u64> = 630_822_816_000_000_000..662_380_416_000_000_000;
    RANGE.contains(&ticks).then(|| ((ticks - EPOCH) / 10_000_000) as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn object(fixed: usize, inline: u16, heap: &[u8], slots: &[(usize, u32, u32)]) -> Vec<u8> {
        let mut b = vec![0; fixed];
        b[94..96].copy_from_slice(&inline.to_le_bytes());
        for &(at, off, len) in slots {
            b[at..at + 4].copy_from_slice(&off.to_le_bytes());
            b[at + 4..at + 8].copy_from_slice(&(len | 0x8000_0000).to_le_bytes());
        }
        b.extend(vec![0xee; inline as usize]);
        b.extend(heap);
        b
    }

    #[test]
    fn descriptors_are_relative_to_fixed_part_plus_inline_region() {
        let heap: Vec<u8> = "ab\0".encode_utf16().flat_map(u16::to_le_bytes).collect();
        let b = object(200, 7, &heap, &[(120, 0, 6)]);
        let o = Object::new(&b, 200).unwrap();
        assert_eq!(o.slot(120).unwrap().and_then(text).as_deref(), Some("ab"));
        assert_eq!(o.slot(128), Some(None));
        let mut external = object(200, 7, &heap, &[(120, 0, 6 | 0x0200_0000)]);
        let o = Object::new(&external, 200).unwrap();
        assert!(o.external(120) && !o.external(128));
        assert_eq!(o.slot(120).unwrap().and_then(text).as_deref(), Some("ab"));
        external.clear();
        let outside = object(200, 7, &heap, &[(120, 2, 6)]);
        assert_eq!(Object::new(&outside, 200).unwrap().slot(120), None);
        assert!(Object::new(&b[..150], 200).is_none());
    }

    #[test]
    fn references_need_the_repeated_key_and_owner() {
        let mut r = vec![0; 46];
        r[0] = 0x4d;
        r[10..18].copy_from_slice(&5u64.to_le_bytes());
        r[18..22].copy_from_slice(&2u32.to_le_bytes());
        r[22..30].copy_from_slice(&7u64.to_le_bytes());
        r[30..38].copy_from_slice(&5u64.to_le_bytes());
        r[38..46].copy_from_slice(&7u64.to_le_bytes());
        assert_eq!(reference(&r, 0x4d, 2), Some((5, 7)));
        assert_eq!(reference(&r, 0x4d, 3), None);
        r[38] = 8;
        assert_eq!(reference(&r, 0x4d, 2), None);
    }

    #[test]
    fn ticks_outside_the_plausible_range_are_no_time() {
        assert_eq!(unix(621_355_968_000_000_000 + 17_930_000_000_000_000), Some(1_793_000_000));
        assert_eq!(unix(0), None);
        assert_eq!(unix(u64::MAX), None);
    }
}
