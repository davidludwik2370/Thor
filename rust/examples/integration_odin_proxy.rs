//! Native counterpart of the integration-only runtime compiler proxy.
use std::{env, path::Path, process::{self, Command}};
fn main() {
    let native = env::var_os("THOR_INTEGRATION_ODIN").expect("selected Odin backend");
    let mut args: Vec<_> = env::args_os().skip(1).collect();
    let compile = args.first().and_then(|arg| arg.to_str()).is_some_and(|arg|
        ["build", "run", "test", "check"].contains(&arg));
    let tool = if compile {
        if let Some(source) = args.get(1).filter(|arg| !arg.to_string_lossy().starts_with('-')) {
            let path = Path::new(source);
            let converted = path.with_extension("thor");
            if path.extension().is_some_and(|ext| ext == "odin") && !path.exists() && converted.exists() {
                args[1] = converted.into_os_string();
            }
        }
        let boundary = args.iter().position(|arg| arg == "--").unwrap_or(args.len());
        args.splice(boundary..boundary, ["--odin-compatible".into(), "--odin".into(), native.clone()]);
        env::var_os("THOR_INTEGRATION_THOR").expect("selected Thor compiler")
    } else { native };
    let status = Command::new(tool).args(args).status().expect("runtime compiler launch");
    process::exit(status.code().unwrap_or(1));
}
