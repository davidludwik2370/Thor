// Embed the implementation identity in the executable. Reading its pathname
// later can observe a replacement binary while an older process is still alive.
use sha2::{Digest, Sha256};
use std::{env, fs, path::{Path, PathBuf}, process::Command};

fn sources(directory:&Path, files:&mut Vec<PathBuf>) -> std::io::Result<()> {
    for entry in fs::read_dir(directory)? {
        let entry=entry?;
        if entry.file_type()?.is_dir(){sources(&entry.path(),files)?;}
        else if entry.path().extension().is_some_and(|s|s=="rs"){files.push(entry.path());}
    }
    Ok(())
}
fn main() {
    // Match the existing compiler worker/Unix main-thread stack budget. MSVC's
    // smaller default stack otherwise aborts on valid deeply nested types in
    // the serial pipeline. This reserves address space, committed on demand;
    // it does not change any generated Odin program's stack or runtime code.
    if env::var("CARGO_CFG_TARGET_OS").as_deref()==Ok("windows") {
        let flag=if env::var("CARGO_CFG_TARGET_ENV").as_deref()==Ok("msvc") {
            "/STACK:8388608"
        } else {"-Wl,--stack,8388608"};
        println!("cargo:rustc-link-arg-bin=thor-rust={flag}");
    }
    println!("cargo:rerun-if-changed=src");
    let mut files=vec![PathBuf::from("build.rs"),PathBuf::from("Cargo.toml"),PathBuf::from("Cargo.lock")];
    sources(Path::new("src"),&mut files).expect("compiler sources");
    files.sort();
    let mut hash=Sha256::new();
    for file in files {
        println!("cargo:rerun-if-changed={}",file.display());
        hash.update(file.to_string_lossy().as_bytes());hash.update([0]);
        hash.update(fs::read(file).expect("compiler input"));hash.update([0]);
    }
    for name in ["TARGET","HOST","PROFILE","OPT_LEVEL","DEBUG","CARGO_ENCODED_RUSTFLAGS"] {
        hash.update(name.as_bytes());hash.update([0]);
        hash.update(env::var_os(name).unwrap_or_default().to_string_lossy().as_bytes());hash.update([0]);
    }
    let mut features=env::vars().filter(|(name,_)|name.starts_with("CARGO_FEATURE_")).collect::<Vec<_>>();
    features.sort();
    for (name,value) in features {hash.update(name.as_bytes());hash.update([0]);hash.update(value.as_bytes());hash.update([0]);}
    let compiler=Command::new(env::var_os("RUSTC").expect("Rust compiler")).args(["--version","--verbose"])
        .output().expect("Rust compiler identity");
    if !compiler.status.success(){panic!("could not identify Rust compiler");}
    hash.update(compiler.stdout);
    let identity=hash.finalize().iter().map(|byte|format!("{byte:02x}")).collect::<String>();
    println!("cargo:rustc-env=THOR_COMPILER_REVISION={identity}");
}
