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
    fs::write(target, output.stdout).expect("write generated atlas names");
}
