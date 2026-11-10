//! Independent package construction and native dependency relocation.
pub mod deps;
pub mod icons;
pub mod package;
pub mod policy;
use crate::{
    diagnostic::Result,
    project::{print_diagnostic, Options as ProjectOptions, Project},
};
pub use package::{Options, Publisher};
use serde_json::json;
use std::{
    fs,
    path::{Path, PathBuf},
};

struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn scratch(parent: &Path) -> Result<Scratch> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    loop {
        let path = parent.join(format!(
            ".thor-publish-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        match fs::create_dir(&path) {
            Ok(()) => return Ok(Scratch(path)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.into()),
        }
    }
}
fn host() -> (&'static str, &'static str) {
    (
        if cfg!(target_os = "macos") {
            "darwin"
        } else {
            std::env::consts::OS
        },
        match std::env::consts::ARCH {
            "x86_64" => "amd64",
            "aarch64" => "arm64",
            arch => arch,
        },
    )
}
pub fn run(options: ProjectOptions, settings: Options) -> Result<i32> {
    let mut diagnostics = vec![];
    let mut project = None;
    let result = (|| -> Result<i32> {
        // Keep the reference's toolchain-first validation order.
        crate::frontend::executable(&options.odin)?;
        let (system, arch) = host();
        let mut publisher =
            Publisher::prepare(&options.entry, &options.flags, &settings, system, arch)?;
        if !options.flags.iter().any(|flag| flag == "-ignore-warnings") {
            diagnostics.extend(publisher.warnings.iter().map(|message|json!({"type":if options.flags.iter().any(|flag|flag=="-warnings-as-errors"){"error"}else{"warning"},"stage":"publish","pos":null,"msgs":[message]})));
            if diagnostics.iter().any(|error| error["type"] == "error") {
                return Ok(1);
            }
        }
        fs::create_dir_all(publisher.output.parent().unwrap())?;
        let scratch = scratch(publisher.output.parent().unwrap())?;
        project = Some(Project::new(options.clone())?);
        let project = project.as_mut().unwrap();
        project.publish_layout = Some(system.into());
        project.collected_diagnostics = Some(Default::default());
        let target = project.lower()?;
        let mut flags = publisher.native_flags(&project.native_flags(), &scratch.0)?;
        let directory = scratch.0.join("compiled");
        fs::create_dir(&directory)?;
        let executable = directory.join(if system == "windows" {
            "program.exe"
        } else {
            "program"
        });
        flags.push(format!("-out:{}", executable.display()));
        project.native_override = Some(flags);
        let status = project.compile(&target)?;
        if status == 0 {
            let output = publisher.package(&executable, &scratch.0)?;
            println!("{}", output.display());
        }
        Ok(status)
    })();
    let editor_info=project.as_ref().map(|project|project.editor_info.clone()).unwrap_or_default();
    if let Some(project) = project {
        if let Some(collected) = project.collected_diagnostics {
            diagnostics.extend(collected.into_inner());
        }
    }
    let status = match result {
        Ok(status) => status,
        Err(error) => {
            let status = if error.unsupported {
                3
            } else {
                error.exit_status
            };
            if error.diagnostics.is_empty() {
                diagnostics.push(
                    json!({"type":"error","stage":"publish","pos":null,"msgs":[error.message]}),
                );
            } else {
                diagnostics.extend(error.diagnostics);
            }
            i32::from(status)
        }
    };
    if options.json {
        let mut packet = json!({"error_count":diagnostics.iter().filter(|error|error["type"]=="error").count(),"errors":diagnostics});
        if options.editor_info {
            packet["slice_access"] = json!(editor_info);
        }
        eprintln!("{packet}");
    } else {
        for error in &diagnostics {
            print_diagnostic(error);
        }
    }
    Ok(status)
}
