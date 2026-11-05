use std::{env, fs, path::PathBuf, process::Command};

fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap()).join("../..");
    for input in ["vendor/hxprobe/src", "vendor/hxprobe/Cargo.toml", "vendor/hxprobe/Cargo.lock",
                  "vendor/hxprobe/build.rs", "ops/hxprobe-source-digest.py",
                  "spec/outlook/contact-observation.v1.json"] {
        println!("cargo:rerun-if-changed={}", root.join(input).display());
    }
    let stamp = Command::new("python3")
        .arg("-c")
        .arg("import pathlib, runpy, sys; root = pathlib.Path(sys.argv[1]); print(runpy.run_path(str(root / 'ops/hxprobe-source-digest.py'))['digest'](root / 'vendor/hxprobe'), end='')")
        .arg(&root)
        .output()
        .expect("Python 3 is required to stamp the contact capability");
    assert!(stamp.status.success(), "contact source digest failed");
    fs::write(PathBuf::from(env::var_os("OUT_DIR").unwrap()).join("contact_source_digest"),
              stamp.stdout).expect("write contact source digest");
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
