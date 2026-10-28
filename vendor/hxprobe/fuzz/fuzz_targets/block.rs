#![no_main]
use libfuzzer_sys::fuzz_target;

fuzz_target!(|bytes: &[u8]| hxprobe_fuzz::fuzz_block(bytes));
