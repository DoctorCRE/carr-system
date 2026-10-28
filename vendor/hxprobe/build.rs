use std::{env, fs, path::PathBuf, process::Command};

fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap()).join("../..");
    let atlas = root.join("spec/outlook/hxstore-atlas.json");
    let generator = root.join("ops/hxstore-atlas.py");
    println!("cargo:rerun-if-changed={}", atlas.display());
    println!("cargo:rerun-if-changed={}", generator.display());
    let output = Command::new("python3")
        .arg(generator)
        .arg("rust")
        .arg(atlas)
        .output()
        .expect("Python 3 is required to generate atlas names");
    assert!(output.status.success(), "store atlas validation failed");
    let target = PathBuf::from(env::var_os("OUT_DIR").unwrap()).join("atlas_names.rs");
    fs::write(target, &output.stdout).expect("write generated atlas names");

    // Cached decode facts are valid only for the decoder source that wrote
    // them, so the cache records a digest of every source input.
    let crate_dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    println!("cargo:rerun-if-changed={}", crate_dir.join("src").display());
    let mut sources: Vec<PathBuf> = fs::read_dir(crate_dir.join("src"))
        .expect("read src")
        .map(|entry| entry.expect("src entry").path())
        .collect();
    sources.sort();
    sources.push(crate_dir.join("Cargo.lock"));
    let mut digest = Fnv(0xcbf2_9ce4_8422_2325);
    digest.feed(&output.stdout);
    for path in sources {
        digest.feed(path.file_name().unwrap().to_string_lossy().as_bytes());
        digest.feed(&fs::read(&path).expect("read source"));
    }
    println!("cargo:rustc-env=HXPROBE_FACTS_SOURCE={:016x}", digest.0);
}

/// FNV-1a, length-delimited, so the build needs no hashing crate.
struct Fnv(u64);

impl Fnv {
    fn feed(&mut self, bytes: &[u8]) {
        for &b in (bytes.len() as u64).to_le_bytes().iter().chain(bytes) {
            self.0 = (self.0 ^ b as u64).wrapping_mul(0x0100_0000_01b3);
        }
    }
}
